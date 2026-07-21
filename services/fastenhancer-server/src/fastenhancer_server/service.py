"""Authenticated version-1 bidirectional enhancement RPC."""

from __future__ import annotations

import asyncio
import hmac
from collections.abc import AsyncIterator
from contextlib import suppress

import grpc
from fastenhancer.v1 import enhancement_pb2 as pb

from .audio import HOP_SAMPLES, SAMPLE_RATE_HZ
from .metrics import ServerMetrics
from .model import ALGORITHMIC_DELAY_SAMPLES, StreamingModel
from .scheduler import BatchScheduler
from .session import OutputAudio, RPCFailure, StreamCompletion, StreamSession
from .settings import ServerSettings

PROTOCOL_VERSION = "1"
MAX_STREAM_ID_BYTES = 128
MAX_METADATA_ENTRIES = 16
MAX_METADATA_KEY_BYTES = 64
MAX_METADATA_VALUE_BYTES = 256


class ProtocolViolation(ValueError):
    def __init__(self, status: grpc.StatusCode, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class StreamRegistry:
    def __init__(self, max_active_streams: int) -> None:
        self._max_active_streams = max_active_streams
        self._sessions: dict[str, StreamSession] = {}
        self._lock = asyncio.Lock()

    async def add(self, session: StreamSession) -> None:
        async with self._lock:
            if session.stream_id in self._sessions:
                raise ProtocolViolation(grpc.StatusCode.ALREADY_EXISTS, "stream ID is active")
            if len(self._sessions) >= self._max_active_streams:
                raise ProtocolViolation(
                    grpc.StatusCode.RESOURCE_EXHAUSTED, "maximum active streams reached"
                )
            self._sessions[session.stream_id] = session

    async def remove(self, session: StreamSession) -> None:
        async with self._lock:
            if self._sessions.get(session.stream_id) is session:
                del self._sessions[session.stream_id]


class EnhancementService:
    def __init__(
        self,
        *,
        model: StreamingModel,
        scheduler: BatchScheduler,
        settings: ServerSettings,
        metrics: ServerMetrics,
    ) -> None:
        self._model = model
        self._scheduler = scheduler
        self._settings = settings
        self._metrics = metrics
        self._registry = StreamRegistry(settings.max_active_streams)

    def _authenticated(self, context: grpc.aio.ServicerContext[object, object]) -> bool:
        expected = f"Bearer {self._settings.api_token}".encode()
        supplied = b""
        for item in context.invocation_metadata():
            if item.key.lower() == "authorization" and isinstance(item.value, str):
                supplied = item.value.encode()
                break
        return hmac.compare_digest(supplied, expected)

    async def _require_auth(self, context: grpc.aio.ServicerContext[object, object]) -> None:
        if not self._authenticated(context):
            self._metrics.auth_failures.inc()
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "authentication required")

    def _capabilities(self) -> pb.StreamAccepted:
        return pb.StreamAccepted(
            protocol_version=PROTOCOL_VERSION,
            model_name=self._model.model_name,
            model_revision=self._model.model_revision,
            model_sha256=self._model.checkpoint_sha256,
            sample_rate_hz=SAMPLE_RATE_HZ,
            channels=1,
            sample_format=pb.SAMPLE_FORMAT_PCM_S16LE,
            hop_samples=HOP_SAMPLES,
            algorithmic_delay_samples=ALGORITHMIC_DELAY_SAMPLES,
            cuda_device=self._model.cuda_device_name,
            max_audio_chunk_samples=self._settings.max_audio_chunk_samples,
        )

    async def GetCapabilities(
        self, request: pb.GetCapabilitiesRequest, context: grpc.aio.ServicerContext[object, object]
    ) -> pb.GetCapabilitiesResponse:
        del request
        await self._require_auth(context)
        return pb.GetCapabilitiesResponse(
            capabilities=self._capabilities(),
            max_active_streams=self._settings.max_active_streams,
        )

    def _validate_start(self, message: pb.ClientMessage) -> pb.StartStream:
        if message.WhichOneof("body") != "start":
            raise ProtocolViolation(
                grpc.StatusCode.FAILED_PRECONDITION, "StartStream must be first"
            )
        start = message.start
        if start.protocol_version != PROTOCOL_VERSION:
            raise ProtocolViolation(
                grpc.StatusCode.FAILED_PRECONDITION, "unsupported protocol version"
            )
        stream_id_size = len(start.stream_id.encode("utf-8"))
        if stream_id_size == 0 or stream_id_size > MAX_STREAM_ID_BYTES:
            raise ProtocolViolation(grpc.StatusCode.INVALID_ARGUMENT, "invalid stream ID length")
        if start.sample_rate_hz != SAMPLE_RATE_HZ or start.channels != 1:
            raise ProtocolViolation(grpc.StatusCode.INVALID_ARGUMENT, "audio must be 16000 Hz mono")
        if start.sample_format != pb.SAMPLE_FORMAT_PCM_S16LE:
            raise ProtocolViolation(grpc.StatusCode.INVALID_ARGUMENT, "audio must be PCM-S16LE")
        if len(start.metadata) > MAX_METADATA_ENTRIES:
            raise ProtocolViolation(grpc.StatusCode.INVALID_ARGUMENT, "too many metadata entries")
        for key, value in start.metadata.items():
            if not key or len(key.encode()) > MAX_METADATA_KEY_BYTES:
                raise ProtocolViolation(grpc.StatusCode.INVALID_ARGUMENT, "invalid metadata key")
            if len(value.encode()) > MAX_METADATA_VALUE_BYTES:
                raise ProtocolViolation(grpc.StatusCode.INVALID_ARGUMENT, "metadata value too long")
        return start

    async def Enhance(
        self,
        request_iterator: AsyncIterator[pb.ClientMessage],
        context: grpc.aio.ServicerContext[object, object],
    ) -> AsyncIterator[pb.ServerMessage]:
        await self._require_auth(context)
        session: StreamSession | None = None
        registered = False
        reader: asyncio.Task[None] | None = None
        try:
            try:
                first = await asyncio.wait_for(
                    anext(request_iterator), timeout=self._settings.stream_idle_timeout_s
                )
            except StopAsyncIteration:
                await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "StartStream is required")
                return
            except TimeoutError:
                await context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, "stream start timed out")
                return
            try:
                start = self._validate_start(first)
                queue_hops = max(
                    1,
                    self._settings.max_pending_audio_ms_per_stream
                    * SAMPLE_RATE_HZ
                    // 1000
                    // HOP_SAMPLES,
                )
                output_hops = max(
                    1,
                    self._settings.max_output_audio_ms_per_stream
                    * SAMPLE_RATE_HZ
                    // 1000
                    // HOP_SAMPLES,
                )
                session = StreamSession(
                    stream_id=start.stream_id,
                    input_start_sample=start.input_start_sample,
                    caches=self._model.initialize_stream(),
                    input_queue_hops=queue_hops,
                    output_queue_hops=output_hops,
                )
                await self._registry.add(session)
                registered = True
            except ProtocolViolation as exc:
                self._metrics.protocol_errors.labels(code=exc.status.name).inc()
                self._metrics.stream_rejections.labels(reason=exc.status.name).inc()
                await context.abort(exc.status, exc.detail)
                return
            self._metrics.active_streams.inc()
            self._metrics.streams.labels(result="opened").inc()
            reader = asyncio.create_task(
                self._read_requests(request_iterator, session), name="enhancement-request-reader"
            )
            yield pb.ServerMessage(accepted=self._capabilities())
            while True:
                event = await session.output_queue.get()
                if isinstance(event, OutputAudio):
                    self._metrics.output_queue_samples.dec(event.valid_samples)
                    yield pb.ServerMessage(
                        audio=pb.EnhancedAudio(
                            output_sequence=session.output_sequence,
                            output_start_sample=event.start_sample,
                            pcm_s16le=event.pcm_s16le,
                            valid_samples=event.valid_samples,
                        )
                    )
                    session.output_sequence += 1
                elif isinstance(event, StreamCompletion):
                    yield pb.ServerMessage(
                        ended=pb.StreamEnded(
                            input_samples=event.input_samples,
                            output_samples=event.output_samples,
                            flushed=event.flushed,
                        )
                    )
                    self._metrics.streams.labels(result="completed").inc()
                    break
                elif isinstance(event, RPCFailure):
                    self._metrics.protocol_errors.labels(code=event.status.name).inc()
                    await context.abort(event.status, event.detail)
                    return
        except asyncio.CancelledError:
            raise
        finally:
            if session is not None:
                session.cancel()
                while not session.input_queue.empty():
                    work = session.input_queue.get_nowait()
                    if work.source is not None:
                        self._metrics.input_queue_samples.dec(HOP_SAMPLES)
                while not session.output_queue.empty():
                    remaining_event = session.output_queue.get_nowait()
                    if isinstance(remaining_event, OutputAudio):
                        self._metrics.output_queue_samples.dec(remaining_event.valid_samples)
                if registered:
                    await self._registry.remove(session)
                    self._metrics.active_streams.dec()
            if reader is not None:
                reader.cancel()
                with suppress(asyncio.CancelledError):
                    await reader

    async def _read_requests(
        self, request_iterator: AsyncIterator[pb.ClientMessage], session: StreamSession
    ) -> None:
        saw_end = False
        try:
            while True:
                try:
                    message = await asyncio.wait_for(
                        anext(request_iterator), timeout=self._settings.stream_idle_timeout_s
                    )
                except StopAsyncIteration:
                    if not saw_end:
                        raise ProtocolViolation(
                            grpc.StatusCode.FAILED_PRECONDITION, "EndStream is required"
                        ) from None
                    return
                except TimeoutError:
                    raise ProtocolViolation(
                        grpc.StatusCode.DEADLINE_EXCEEDED, "stream idle timeout"
                    ) from None
                body = message.WhichOneof("body")
                if saw_end:
                    raise ProtocolViolation(
                        grpc.StatusCode.FAILED_PRECONDITION, "message received after EndStream"
                    )
                if body == "start":
                    raise ProtocolViolation(
                        grpc.StatusCode.FAILED_PRECONDITION, "duplicate StartStream"
                    )
                if body == "audio":
                    await self._accept_audio(message.audio, session)
                    continue
                if body != "end":
                    raise ProtocolViolation(
                        grpc.StatusCode.INVALID_ARGUMENT, "empty client message"
                    )
                saw_end = True
                await self._finish_input(session, flush=message.end.flush)
        except ProtocolViolation as exc:
            session.cancel()
            self._emit_failure(session, exc.status, exc.detail)
        except asyncio.CancelledError:
            raise
        except Exception:
            session.cancel()
            self._emit_failure(session, grpc.StatusCode.INTERNAL, "request processing failed")

    async def _accept_audio(self, chunk: pb.AudioChunk, session: StreamSession) -> None:
        size = len(chunk.pcm_s16le)
        if size == 0 or size % 2:
            raise ProtocolViolation(
                grpc.StatusCode.INVALID_ARGUMENT, "audio payload must have positive even length"
            )
        sample_count = size // 2
        if sample_count > self._settings.max_audio_chunk_samples:
            raise ProtocolViolation(grpc.StatusCode.RESOURCE_EXHAUSTED, "audio chunk too large")
        if chunk.sequence != session.expected_sequence:
            raise ProtocolViolation(
                grpc.StatusCode.INVALID_ARGUMENT, "audio sequence is not contiguous"
            )
        if chunk.input_start_sample != session.expected_input_sample:
            raise ProtocolViolation(
                grpc.StatusCode.INVALID_ARGUMENT, "audio offset is not contiguous"
            )
        session.expected_sequence += 1
        session.expected_input_sample += sample_count
        session.input_samples += sample_count
        self._metrics.input_samples.inc(sample_count)
        try:
            hops = session.rechunker.append(chunk.pcm_s16le)
        except BufferError as exc:
            self._metrics.backpressure.labels(queue="input").inc()
            raise ProtocolViolation(grpc.StatusCode.RESOURCE_EXHAUSTED, str(exc)) from exc
        for hop in hops:
            await session.enqueue_source(hop)
            self._metrics.input_queue_samples.inc(HOP_SAMPLES)
            self._scheduler.notify(session)

    async def _finish_input(self, session: StreamSession, *, flush: bool) -> None:
        if not flush:
            await session.enqueue_no_flush()
            self._scheduler.notify(session)
            return
        partial = session.rechunker.flush_partial()
        if partial is not None:
            await session.enqueue_source(partial)
            self._metrics.input_queue_samples.inc(HOP_SAMPLES)
            self._scheduler.notify(session)
        if session.input_samples == 0:
            self._scheduler.complete_empty_flush(session)
            return
        await session.enqueue_flush()
        self._scheduler.notify(session)

    def _emit_failure(self, session: StreamSession, status: grpc.StatusCode, detail: str) -> None:
        while not session.output_queue.empty():
            with suppress(asyncio.QueueEmpty):
                event = session.output_queue.get_nowait()
                if isinstance(event, OutputAudio):
                    self._metrics.output_queue_samples.dec(event.valid_samples)
        session.emit_nowait(RPCFailure(status, detail))
