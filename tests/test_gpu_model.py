from __future__ import annotations

from pathlib import Path

import pytest
import torch
from fastenhancer_server.model import FastEnhancerBStreamingModel
from fastenhancer_upstream.models.model import ONNXModel as UpstreamStreamingModel

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_GPU = "NVIDIA RTX A6000"


def a6000_device() -> str:
    matches = [
        index
        for index in range(torch.cuda.device_count())
        if torch.cuda.get_device_name(index) == REQUIRED_GPU
    ]
    if len(matches) != 1:
        pytest.fail(f"expected exactly one visible {REQUIRED_GPU}, found {matches}")
    return f"cuda:{matches[0]}"


def load_model() -> FastEnhancerBStreamingModel:
    return FastEnhancerBStreamingModel(
        checkpoint_path=ROOT / "models/prepared/00500.pth",
        manifest_path=ROOT / "models/manifest.lock.json",
        config_path=ROOT / "models/prepared/config.yaml",
        cuda_device=a6000_device(),
        required_device_name=REQUIRED_GPU,
    )


@pytest.mark.gpu
def test_real_model_batch_parity_and_device(monkeypatch: pytest.MonkeyPatch) -> None:
    device = torch.device(a6000_device())
    memory_before = torch.cuda.memory_allocated(device)
    model = load_model()
    assert torch.cuda.memory_allocated(device) > memory_before
    transforms_seen: set[str] = set()
    real_rfft = torch.fft.rfft
    real_ifft = torch.fft.ifft

    def checked_rfft(value: torch.Tensor, *args: object, **kwargs: object) -> torch.Tensor:
        assert value.device == device
        transforms_seen.add("rfft")
        return real_rfft(value, *args, **kwargs)  # type: ignore[arg-type]

    def checked_ifft(value: torch.Tensor, *args: object, **kwargs: object) -> torch.Tensor:
        assert value.device == device
        transforms_seen.add("ifft")
        return real_ifft(value, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(torch.fft, "rfft", checked_rfft)
    monkeypatch.setattr(torch.fft, "ifft", checked_ifft)
    generator = torch.Generator().manual_seed(87)
    first = torch.randn(8, 256, generator=generator) * 0.05
    second = torch.randn(8, 256, generator=generator) * 0.05
    caches = [model.initialize_stream() for _ in range(8)]
    _, caches = model.infer_batch(first, caches)
    batched, _ = model.infer_batch(second, caches)
    references: list[torch.Tensor] = []
    for index in range(8):
        cache = model.initialize_stream()
        _, cache_list = model.infer_batch(first[index : index + 1], [cache])
        output, _ = model.infer_batch(second[index : index + 1], cache_list)
        references.append(output[0])
    torch.cuda.synchronize(torch.device(a6000_device()))
    reference = torch.stack(references)
    assert batched.device.type == "cuda"
    difference = batched.sub(reference)
    signal_rms = reference.square().mean().sqrt()
    error_rms = difference.square().mean().sqrt()
    snr_db = 20 * torch.log10(signal_rms / error_rms)
    # cuDNN selects different GRU kernels for B=8 and B=1. Across the locked
    # CUDA 12.8/cuDNN 9 runtime the measured float32 difference remains below
    # 2.5e-5 absolute and above 55 dB SNR.
    assert difference.abs().max() < 2.5e-5
    assert snr_db > 55
    assert torch.isfinite(batched).all()
    assert transforms_seen == {"rfft", "ifft"}


@pytest.mark.gpu
def test_streaming_matches_official_export_wrapper_reference() -> None:
    model = load_model()
    checkpoint = torch.load(
        ROOT / "models/prepared/00500.pth", map_location="cpu", weights_only=True
    )
    upstream = UpstreamStreamingModel(
        channels=48,
        kernel_size=[8, 3, 3],
        stride=4,
        rnnformer_kwargs={
            "num_blocks": 3,
            "channels": 36,
            "freq": 24,
            "num_heads": 4,
            "eps": 1.0e-5,
            "positional_embedding": "train",
            "attn_bias": False,
            "post_act": False,
            "pre_norm": False,
        },
        pre_post_init="linear_fixed",
        n_fft=512,
        hop_size=256,
        win_size=512,
        window="hann",
        stft_normalized=False,
        mask=None,
        activation="SiLU",
        activation_kwargs={"inplace": True},
        input_compression=0.3,
        normalize_final_conv=True,
        weight_norm=True,
        resnet=False,
    )
    upstream.load_state_dict(checkpoint["model"], strict=True)
    upstream.remove_weight_reparameterizations()
    upstream.eval().requires_grad_(False).to(a6000_device())
    generator = torch.Generator().manual_seed(91)
    source = torch.randn(1, 4096, generator=generator).mul_(0.03)
    padded = torch.nn.functional.pad(source, (0, 512))
    seed = torch.zeros(1, device=a6000_device())
    stft_cache, istft_cache = upstream.stft.initialize_cache(seed)
    rnn_caches = upstream.initialize_cache(seed)
    upstream_streamed: list[torch.Tensor] = []
    with torch.inference_mode():
        for start in range(0, source.shape[1] + 256, 256):
            hop = padded[:, start : start + 256].to(a6000_device())
            spec, stft_cache = upstream.stft(hop, stft_cache)
            enhanced, *rnn_caches = upstream(spec, *rnn_caches)
            output, istft_cache = upstream.stft.inverse(enhanced, istft_cache)
            upstream_streamed.append(output)
    cache = model.initialize_stream()
    streamed: list[torch.Tensor] = []
    for start in range(0, source.shape[1] + 256, 256):
        output, updated = model.infer_batch(padded[:, start : start + 256], [cache])
        cache = updated[0]
        streamed.append(output)
    streaming_output = torch.cat(streamed, dim=1)
    upstream_output = torch.cat(upstream_streamed, dim=1)
    assert torch.allclose(streaming_output, upstream_output, rtol=1e-5, atol=1e-7)
