"""Real-model CUDA startup checks required before readiness."""

from __future__ import annotations

import torch

from .model import HOP_SAMPLES, FastEnhancerBStreamingModel


def validate_state_isolation(model: FastEnhancerBStreamingModel) -> None:
    first = torch.zeros(2, HOP_SAMPLES, dtype=torch.float32)
    first[0, 23] = 0.75
    first[1, 191] = -0.5
    initial = [model.initialize_stream(), model.initialize_stream()]
    _, batch_caches = model.infer_batch(first, initial)
    batch_output, _ = model.infer_batch(torch.zeros_like(first), batch_caches)
    references: list[torch.Tensor] = []
    for index in range(2):
        cache = model.initialize_stream()
        _, updated = model.infer_batch(first[index : index + 1], [cache])
        output, _ = model.infer_batch(torch.zeros(1, HOP_SAMPLES), updated)
        references.append(output[0])
    reference = torch.stack(references)
    difference = batch_output.sub(reference)
    error_rms = difference.square().mean().sqrt()
    signal_rms = reference.square().mean().sqrt()
    snr_db = 20 * torch.log10(signal_rms / error_rms)
    if difference.abs().max() >= 5e-5 or snr_db <= 60:
        raise RuntimeError("startup state-isolation batch parity failed")
    if not torch.isfinite(batch_output).all():
        raise RuntimeError("startup self-test produced non-finite audio")
