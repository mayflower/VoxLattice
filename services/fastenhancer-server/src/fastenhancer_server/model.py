"""Concrete CUDA-only FastEnhancer-B streaming model."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import torch
import yaml
from fastenhancer_upstream.models.fastenhancer_b import ONNXModel
from torch import Tensor

SAMPLE_RATE_HZ = 16_000
N_FFT = 512
HOP_SAMPLES = 256
ALGORITHMIC_DELAY_SAMPLES = 256
RNN_BLOCKS = 3
RF_FREQ = 24
RF_HIDDEN = 36
EXPECTED_MODEL_NAME = "FastEnhancer-B"


@dataclass(frozen=True, slots=True)
class StreamCaches:
    stft: Tensor
    istft: Tensor
    rnn: tuple[Tensor, Tensor, Tensor]

    def assert_layout(self, *, batch_size: int = 1, device_type: str | None = None) -> None:
        if tuple(self.stft.shape) != (batch_size, HOP_SAMPLES):
            raise RuntimeError(f"invalid STFT cache shape {tuple(self.stft.shape)}")
        if tuple(self.istft.shape) != (batch_size, HOP_SAMPLES):
            raise RuntimeError(f"invalid iSTFT cache shape {tuple(self.istft.shape)}")
        if len(self.rnn) != RNN_BLOCKS:
            raise RuntimeError("invalid RNN cache count")
        expected_rnn = (1, batch_size * RF_FREQ, RF_HIDDEN)
        if any(tuple(cache.shape) != expected_rnn for cache in self.rnn):
            raise RuntimeError("invalid RNN cache shape")
        tensors = (self.stft, self.istft, *self.rnn)
        if device_type is not None and any(tensor.device.type != device_type for tensor in tensors):
            raise RuntimeError(f"cache is not on required {device_type} device")
        if any(tensor.dtype != torch.float32 for tensor in tensors):
            raise RuntimeError("all caches must be float32")


class StreamingModel(Protocol):
    model_name: str
    model_revision: str
    checkpoint_sha256: str
    cuda_device_name: str

    def initialize_stream(self) -> StreamCaches: ...

    def infer_batch(
        self, wav_hops: Tensor, stream_caches: Sequence[StreamCaches]
    ) -> tuple[Tensor, list[StreamCaches]]: ...


def combine_stream_caches(caches: Sequence[StreamCaches]) -> StreamCaches:
    if not caches:
        raise ValueError("cannot combine an empty cache sequence")
    for cache in caches:
        cache.assert_layout()
    combined = StreamCaches(
        stft=torch.cat([cache.stft for cache in caches], dim=0),
        istft=torch.cat([cache.istft for cache in caches], dim=0),
        rnn=(
            torch.cat([cache.rnn[0] for cache in caches], dim=1),
            torch.cat([cache.rnn[1] for cache in caches], dim=1),
            torch.cat([cache.rnn[2] for cache in caches], dim=1),
        ),
    )
    combined.assert_layout(batch_size=len(caches))
    return combined


def split_stream_caches(caches: StreamCaches, batch_size: int) -> list[StreamCaches]:
    caches.assert_layout(batch_size=batch_size)
    result: list[StreamCaches] = []
    for index in range(batch_size):
        start = index * RF_FREQ
        result.append(
            StreamCaches(
                stft=caches.stft[index : index + 1],
                istft=caches.istft[index : index + 1],
                rnn=(
                    caches.rnn[0][:, start : start + RF_FREQ],
                    caches.rnn[1][:, start : start + RF_FREQ],
                    caches.rnn[2][:, start : start + RF_FREQ],
                ),
            )
        )
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class FastEnhancerBStreamingModel:
    """One immutable model instance with explicit per-stream cache inputs."""

    def __init__(
        self,
        *,
        checkpoint_path: Path,
        manifest_path: Path,
        config_path: Path,
        cuda_device: str = "cuda:0",
    ) -> None:
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is required; CPU production inference is forbidden")
        self.device = torch.device(cuda_device)
        if self.device.type != "cuda" or self.device.index is None:
            raise RuntimeError("CUDA_DEVICE must be an explicit cuda:N device")
        if self.device.index >= torch.cuda.device_count():
            raise RuntimeError(f"configured CUDA device does not exist: {self.device}")
        self.cuda_device_name = torch.cuda.get_device_name(self.device)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self._validate_manifest(manifest)
        actual_hash = _sha256(checkpoint_path)
        if actual_hash != manifest["checkpoint_sha256"]:
            raise RuntimeError("checkpoint SHA-256 does not match locked manifest")
        self.model_name = EXPECTED_MODEL_NAME
        self.model_revision = str(manifest["upstream_commit"])
        self.checkpoint_sha256 = actual_hash
        model_kwargs = self._load_and_validate_config(config_path)
        network = ONNXModel(**model_kwargs)
        checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
        if not isinstance(checkpoint, dict) or "model" not in checkpoint:
            raise RuntimeError("checkpoint does not contain a model state dict")
        network.load_state_dict(checkpoint["model"], strict=True)
        network.remove_weight_reparameterizations()  # type: ignore[no-untyped-call]
        network.eval()
        network.requires_grad_(False)
        self._network = network.to(device=self.device, dtype=torch.float32)
        self._network.flatten_parameters()  # type: ignore[no-untyped-call]
        self._inference_lock = threading.Lock()
        self._validate_parameters()

    @staticmethod
    def _validate_manifest(manifest: dict[str, object]) -> None:
        expected = {
            "model": EXPECTED_MODEL_NAME,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "n_fft": N_FFT,
            "hop_samples": HOP_SAMPLES,
            "algorithmic_delay_samples": ALGORITHMIC_DELAY_SAMPLES,
            "rnn_blocks": RNN_BLOCKS,
            "rf_freq": RF_FREQ,
            "rf_hidden": RF_HIDDEN,
        }
        for key, value in expected.items():
            if manifest.get(key) != value:
                raise RuntimeError(f"locked model invariant mismatch for {key}")

    @staticmethod
    def _load_and_validate_config(path: Path) -> dict[str, Any]:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        kwargs = config["model_kwargs"]
        expected = {
            "n_fft": N_FFT,
            "hop_size": HOP_SAMPLES,
            "win_size": N_FFT,
        }
        for key, value in expected.items():
            if kwargs.get(key) != value:
                raise RuntimeError(f"released config invariant mismatch for {key}")
        rf = kwargs.get("rnnformer_kwargs", {})
        if (rf.get("num_blocks"), rf.get("freq"), rf.get("channels")) != (
            RNN_BLOCKS,
            RF_FREQ,
            RF_HIDDEN,
        ):
            raise RuntimeError("released RNNFormer layout differs from the validated Base model")
        return dict(kwargs)

    def _validate_parameters(self) -> None:
        parameters = tuple(self._network.parameters())
        if not parameters or any(parameter.device != self.device for parameter in parameters):
            raise RuntimeError("model parameters are not entirely on the configured CUDA device")
        if any(parameter.dtype != torch.float32 for parameter in parameters):
            raise RuntimeError("model parameters are not entirely float32")

    def initialize_stream(self) -> StreamCaches:
        seed = torch.zeros(1, device=self.device, dtype=torch.float32)
        transform = cast(Any, self._network.stft)
        stft, istft = transform.initialize_cache(seed)
        rnn = tuple(self._network.initialize_cache(seed))
        if len(rnn) != 3:
            raise RuntimeError("model returned an unexpected number of RNN caches")
        caches = StreamCaches(stft=stft, istft=istft, rnn=(rnn[0], rnn[1], rnn[2]))
        caches.assert_layout(device_type="cuda")
        return caches

    def infer_batch(
        self, wav_hops: Tensor, stream_caches: Sequence[StreamCaches]
    ) -> tuple[Tensor, list[StreamCaches]]:
        batch_size = len(stream_caches)
        if tuple(wav_hops.shape) != (batch_size, HOP_SAMPLES):
            raise ValueError("wav_hops must have shape [batch, 256]")
        combined = combine_stream_caches(stream_caches)
        wav_hops = wav_hops.to(self.device, dtype=torch.float32, non_blocking=True)
        combined = StreamCaches(
            combined.stft.to(self.device),
            combined.istft.to(self.device),
            tuple(cache.to(self.device) for cache in combined.rnn),  # type: ignore[arg-type]
        )
        with self._inference_lock, torch.inference_mode():
            transform = cast(Any, self._network.stft)
            spec, stft = transform(wav_hops, combined.stft)
            spec_out, *rnn = self._network(spec, *combined.rnn)
            wav_out, istft = transform.inverse(spec_out, combined.istft)
        if tuple(wav_out.shape) != (batch_size, HOP_SAMPLES):
            raise RuntimeError("model returned an invalid waveform shape")
        if not torch.isfinite(wav_out).all():
            raise RuntimeError("model produced NaN or infinity")
        updated = StreamCaches(stft=stft, istft=istft, rnn=(rnn[0], rnn[1], rnn[2]))
        updated.assert_layout(batch_size=batch_size, device_type="cuda")
        return wav_out, split_stream_caches(updated, batch_size)

    def warm_up(self, batch_sizes: Sequence[int] = (1, 2, 8, 16)) -> None:
        for batch_size in batch_sizes:
            caches = [self.initialize_stream() for _ in range(batch_size)]
            audio = torch.zeros(batch_size, HOP_SAMPLES, dtype=torch.float32)
            output, updated = self.infer_batch(audio, caches)
            if output.device != self.device or len(updated) != batch_size:
                raise RuntimeError("CUDA warm-up validation failed")
        torch.cuda.synchronize(self.device)
