"""Fair central micro-batch scheduler with one dependent hop per stream."""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

import grpc
import torch

from .audio import HOP_SAMPLES, float32_to_pcm16le
from .metrics import ServerMetrics
from .model import StreamCaches, StreamingModel
from .session import ModelWork, OutputAudio, RPCFailure, StreamCompletion, StreamSession


class BatchScheduler:
    def __init__(
        self,
        model: StreamingModel,
        metrics: ServerMetrics,
        *,
        max_active_streams: int,
        max_batch_size: int,
        max_batch_wait_ms: float,
    ) -> None:
        self.model = model
        self.metrics = metrics
        self.max_batch_size = max_batch_size
        self.max_batch_wait_s = max_batch_wait_ms / 1000.0
        self._ready: asyncio.Queue[StreamSession] = asyncio.Queue(maxsize=max_active_streams)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cuda-inference")
        self._task: asyncio.Task[None] | None = None
        self._closing = False

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("scheduler already started")
        self._task = asyncio.create_task(self._run(), name="fastenhancer-batch-scheduler")

    def notify(self, session: StreamSession) -> None:
        if self._closing or session.cancelled or session.scheduled or session.input_queue.empty():
            return
        session.scheduled = True
        self._ready.put_nowait(session)

    async def close(self) -> None:
        self._closing = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        self._executor.shutdown(wait=True, cancel_futures=True)

    async def _run(self) -> None:
        while True:
            first = await self._ready.get()
            batch_started = time.perf_counter()
            sessions = [first]
            deadline = asyncio.get_running_loop().time() + self.max_batch_wait_s
            while len(sessions) < self.max_batch_size:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    session = await asyncio.wait_for(self._ready.get(), remaining)
                except TimeoutError:
                    break
                sessions.append(session)
            self.metrics.batch_wait.observe(time.perf_counter() - batch_started)
            active: list[tuple[StreamSession, ModelWork]] = []
            for session in sessions:
                session.scheduled = False
                if session.cancelled or session.ended:
                    continue
                try:
                    work = session.input_queue.get_nowait()
                    if work.terminal_no_flush:
                        self.complete_without_flush(session)
                        continue
                    active.append((session, work))
                    if work.source is not None:
                        self.metrics.input_queue_samples.dec(HOP_SAMPLES)
                except asyncio.QueueEmpty:
                    continue
            if not active:
                continue
            self.metrics.batch_size.observe(len(active))
            audio = torch.stack([work.samples for _, work in active])
            caches = [session.caches for session, _ in active]
            loop = asyncio.get_running_loop()
            try:
                (pcm_hops, updated), inference_seconds = await loop.run_in_executor(
                    self._executor, self._timed_inference, audio, caches
                )
            except Exception:
                for session, _ in active:
                    self._fail(session, grpc.StatusCode.INTERNAL, "CUDA inference failed")
                continue
            self.metrics.inference.observe(inference_seconds)
            for index, (session, work) in enumerate(active):
                if session.cancelled or session.ended:
                    continue
                session.caches = updated[index]
                previous = session.pending_source
                previous_enqueued_at = session.pending_enqueued_at
                if work.terminal_flush:
                    session.pending_source = None
                    session.pending_enqueued_at = 0.0
                else:
                    session.pending_source = work.source
                    session.pending_enqueued_at = work.enqueued_at
                if previous is not None:
                    output = pcm_hops[index][: previous.valid_samples * 2]
                    try:
                        session.emit_nowait(
                            OutputAudio(previous.start_sample, output, previous.valid_samples)
                        )
                        self.metrics.output_queue_samples.inc(previous.valid_samples)
                    except asyncio.QueueFull:
                        self.metrics.backpressure.labels(queue="output").inc()
                        self._fail(
                            session,
                            grpc.StatusCode.RESOURCE_EXHAUSTED,
                            "per-stream output queue is full",
                        )
                        continue
                    session.output_samples += previous.valid_samples
                    self.metrics.output_samples.inc(previous.valid_samples)
                    self.metrics.hop_end_to_end.observe(time.perf_counter() - previous_enqueued_at)
                if work.terminal_flush:
                    self._complete(session, flushed=True)
                elif not session.input_queue.empty():
                    self.notify(session)

    def _timed_inference(
        self, audio: torch.Tensor, caches: list[StreamCaches]
    ) -> tuple[tuple[list[bytes], list[StreamCaches]], float]:
        started = time.perf_counter()
        wav, updated = self.model.infer_batch(audio, caches)
        inference_seconds = time.perf_counter() - started
        pcm_hops = [float32_to_pcm16le(wav[index]) for index in range(wav.shape[0])]
        return (pcm_hops, updated), inference_seconds

    def complete_without_flush(self, session: StreamSession) -> None:
        session.pending_source = None
        self._complete(session, flushed=False)

    def complete_empty_flush(self, session: StreamSession) -> None:
        self._complete(session, flushed=True)

    def _complete(self, session: StreamSession, *, flushed: bool) -> None:
        if session.ended:
            return
        session.ended = True
        try:
            session.emit_nowait(
                StreamCompletion(session.input_samples, session.output_samples, flushed)
            )
        except asyncio.QueueFull:
            self._fail(
                session, grpc.StatusCode.RESOURCE_EXHAUSTED, "per-stream output queue is full"
            )

    def _fail(self, session: StreamSession, status: grpc.StatusCode, detail: str) -> None:
        session.cancel()
        while not session.output_queue.empty():
            with suppress(asyncio.QueueEmpty):
                event = session.output_queue.get_nowait()
                if isinstance(event, OutputAudio):
                    self.metrics.output_queue_samples.dec(event.valid_samples)
        session.emit_nowait(RPCFailure(status, detail))
