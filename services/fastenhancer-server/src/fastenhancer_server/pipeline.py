"""Single-stream delay-correct pipeline used by direct validation."""

from __future__ import annotations

from dataclasses import dataclass

import torch

from .audio import HOP_SAMPLES, SourceHop
from .model import StreamCaches, StreamingModel


@dataclass(frozen=True, slots=True)
class EnhancedHop:
    start_sample: int
    samples: torch.Tensor
    valid_samples: int


class StreamingPipeline:
    def __init__(self, model: StreamingModel) -> None:
        self._model = model
        self.caches: StreamCaches = model.initialize_stream()
        self._pending: SourceHop | None = None
        self._closed = False

    def push(self, source: SourceHop) -> EnhancedHop | None:
        if self._closed:
            raise RuntimeError("pipeline is closed")
        if tuple(source.samples.shape) != (HOP_SAMPLES,):
            raise ValueError("source hop must contain exactly 256 padded samples")
        wav, caches = self._model.infer_batch(source.samples.unsqueeze(0), [self.caches])
        self.caches = caches[0]
        previous = self._pending
        self._pending = source
        if previous is None:
            return None
        return EnhancedHop(
            previous.start_sample, wav[0, : previous.valid_samples], previous.valid_samples
        )

    def flush(self) -> EnhancedHop | None:
        if self._closed:
            return None
        self._closed = True
        if self._pending is None:
            return None
        wav, caches = self._model.infer_batch(
            torch.zeros(1, HOP_SAMPLES, dtype=torch.float32), [self.caches]
        )
        self.caches = caches[0]
        pending = self._pending
        self._pending = None
        return EnhancedHop(
            pending.start_sample, wav[0, : pending.valid_samples], pending.valid_samples
        )
