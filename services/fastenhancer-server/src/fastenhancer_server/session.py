"""Per-RPC stream state; never contains a model instance."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import grpc
import torch

from .audio import HOP_SAMPLES, PCMRechunker, SourceHop
from .model import StreamCaches


@dataclass(frozen=True, slots=True)
class ModelWork:
    samples: torch.Tensor
    source: SourceHop | None
    terminal_flush: bool = False
    terminal_no_flush: bool = False
    enqueued_at: float = 0.0


@dataclass(frozen=True, slots=True)
class OutputAudio:
    start_sample: int
    pcm_s16le: bytes
    valid_samples: int


@dataclass(frozen=True, slots=True)
class StreamCompletion:
    input_samples: int
    output_samples: int
    flushed: bool


@dataclass(frozen=True, slots=True)
class RPCFailure:
    status: grpc.StatusCode
    detail: str


OutputEvent = OutputAudio | StreamCompletion | RPCFailure


class StreamSession:
    def __init__(
        self,
        *,
        stream_id: str,
        input_start_sample: int,
        caches: StreamCaches,
        input_queue_hops: int,
        output_queue_hops: int,
    ) -> None:
        self.stream_id = stream_id
        self.input_start_sample = input_start_sample
        self.expected_input_sample = input_start_sample
        self.expected_sequence = 0
        self.input_samples = 0
        self.output_samples = 0
        self.output_sequence = 0
        self.caches = caches
        self.rechunker = PCMRechunker(
            start_sample=input_start_sample,
            max_pending_samples=max(HOP_SAMPLES, input_queue_hops * HOP_SAMPLES),
        )
        self.input_queue: asyncio.Queue[ModelWork] = asyncio.Queue(maxsize=input_queue_hops)
        self.output_queue: asyncio.Queue[OutputEvent] = asyncio.Queue(maxsize=output_queue_hops)
        self.pending_source: SourceHop | None = None
        self.pending_enqueued_at = 0.0
        self.scheduled = False
        self.ended = False
        self.cancelled = False
        self.last_activity = time.monotonic()

    async def enqueue_source(self, source: SourceHop) -> None:
        await self.input_queue.put(
            ModelWork(source.samples, source, enqueued_at=time.perf_counter())
        )

    async def enqueue_flush(self) -> None:
        await self.input_queue.put(
            ModelWork(
                torch.zeros(HOP_SAMPLES, dtype=torch.float32),
                None,
                terminal_flush=True,
                enqueued_at=time.perf_counter(),
            )
        )

    async def enqueue_no_flush(self) -> None:
        await self.input_queue.put(
            ModelWork(
                torch.empty(HOP_SAMPLES, dtype=torch.float32),
                None,
                terminal_no_flush=True,
                enqueued_at=time.perf_counter(),
            )
        )

    def emit_nowait(self, event: OutputEvent) -> None:
        self.output_queue.put_nowait(event)

    def cancel(self) -> None:
        self.cancelled = True
