"""Exact PCM16 conversion and bounded rechunking primitives."""

from __future__ import annotations

import array
import sys
from collections import deque
from dataclasses import dataclass

import torch

SAMPLE_RATE_HZ = 16_000
HOP_SAMPLES = 256


def pcm16le_to_float32(value: bytes) -> torch.Tensor:
    if not value or len(value) % 2:
        raise ValueError("PCM-S16LE must contain a positive, even number of bytes")
    samples = array.array("h")
    samples.frombytes(value)
    if sys.byteorder != "little":
        samples.byteswap()
    return torch.tensor(samples, dtype=torch.float32).div_(32768.0)


def float32_to_pcm16le(value: torch.Tensor) -> bytes:
    if value.ndim != 1:
        raise ValueError("audio tensor must be one-dimensional")
    # Symmetric rounding after scale preserves -32768 while clipping positive full scale.
    quantized = value.detach().to(device="cpu", dtype=torch.float32)
    quantized = quantized.clamp(-1.0, 32767.0 / 32768.0).mul(32768.0).round().to(torch.int16)
    result = array.array("h", quantized.tolist())
    if sys.byteorder != "little":
        result.byteswap()
    return result.tobytes()


@dataclass(frozen=True, slots=True)
class SourceHop:
    start_sample: int
    samples: torch.Tensor
    valid_samples: int


class PCMRechunker:
    """Bounded PCM accumulator that emits exact model-sized source hops."""

    def __init__(self, *, start_sample: int, max_pending_samples: int) -> None:
        if max_pending_samples < HOP_SAMPLES:
            raise ValueError("max_pending_samples must hold at least one hop")
        self._parts: deque[torch.Tensor] = deque()
        self._pending_samples = 0
        self._next_sample = start_sample
        self._max_pending_samples = max_pending_samples

    @property
    def pending_samples(self) -> int:
        return self._pending_samples

    def append(self, pcm_s16le: bytes) -> list[SourceHop]:
        incoming = pcm16le_to_float32(pcm_s16le)
        if self._pending_samples + incoming.numel() > self._max_pending_samples:
            raise BufferError("per-stream input audio buffer is full")
        self._parts.append(incoming)
        self._pending_samples += incoming.numel()
        return self._drain_full_hops()

    def _take(self, count: int) -> torch.Tensor:
        output = torch.empty(count, dtype=torch.float32)
        position = 0
        while position < count:
            part = self._parts[0]
            take = min(count - position, part.numel())
            output[position : position + take].copy_(part[:take])
            position += take
            if take == part.numel():
                self._parts.popleft()
            else:
                self._parts[0] = part[take:]
        self._pending_samples -= count
        return output

    def _drain_full_hops(self) -> list[SourceHop]:
        output: list[SourceHop] = []
        while self._pending_samples >= HOP_SAMPLES:
            samples = self._take(HOP_SAMPLES)
            output.append(SourceHop(self._next_sample, samples, HOP_SAMPLES))
            self._next_sample += HOP_SAMPLES
        return output

    def flush_partial(self) -> SourceHop | None:
        if self._pending_samples == 0:
            return None
        valid = self._pending_samples
        samples = torch.zeros(HOP_SAMPLES, dtype=torch.float32)
        samples[:valid].copy_(self._take(valid))
        hop = SourceHop(self._next_sample, samples, valid)
        self._next_sample += valid
        return hop
