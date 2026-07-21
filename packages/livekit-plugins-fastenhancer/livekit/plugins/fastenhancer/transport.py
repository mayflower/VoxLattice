"""One-thread, one-channel synchronous bidirectional gRPC transport."""

from __future__ import annotations

import logging
import queue
import random
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum

import grpc

from fastenhancer.v1 import enhancement_pb2 as pb
from fastenhancer.v1 import enhancement_pb2_grpc as pb_grpc

EXPECTED_MODEL_NAME = "FastEnhancer-B"
EXPECTED_REVISION = "49ab55a57e3a064d94c6412cd0f0a383a55ca0f8"
EXPECTED_CHECKPOINT_SHA256 = "980ec00d9c3cb0497893c815c718a2fe44970329ae8477d22596d0a1373f2382"


def truncate_utf8(value: object, max_bytes: int) -> str:
    """Return a valid UTF-8 prefix no larger than the protocol byte limit."""
    return str(value).encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


class TransportState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    ACTIVE = "active"
    BACKOFF = "backoff"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class AudioRequest:
    start_sample: int
    pcm_s16le: bytes


@dataclass(frozen=True, slots=True)
class EndRequest:
    flush: bool = True


@dataclass(frozen=True, slots=True)
class StopRequest:
    final: bool = True


@dataclass(frozen=True, slots=True)
class ResetRequest:
    reconnect: bool = True


Request = AudioRequest | EndRequest | StopRequest | ResetRequest


@dataclass(frozen=True, slots=True)
class TransportConfig:
    endpoint: str
    api_key: str | None = field(repr=False)
    tls: bool
    root_certificates: bytes | None
    client_certificate_chain: bytes | None
    client_private_key: bytes | None = field(repr=False)
    connect_timeout_s: float
    reconnect_min_delay_s: float
    reconnect_max_delay_s: float
    max_request_queue_samples: int
    expected_model_revision: str
    expected_model_sha256: str


class GRPCWorker:
    def __init__(
        self,
        config: TransportConfig,
        *,
        metadata_provider: Callable[[], Mapping[str, str]],
        on_audio: Callable[[int, int, bytes], None],
        on_generation: Callable[[int, TransportState], None],
        on_protocol_error: Callable[[int, str], None],
    ) -> None:
        self._config = config
        self._metadata_provider = metadata_provider
        self._on_audio = on_audio
        self._on_generation = on_generation
        self._on_protocol_error = on_protocol_error
        self._requests: queue.Queue[Request] = queue.Queue(
            maxsize=config.max_request_queue_samples + 4
        )
        self._pending_samples = 0
        self._pending_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._stop = threading.Event()
        self._reset = threading.Event()
        self._generation = 0
        self._call: grpc.Call | None = None
        self._channel = self._create_channel()
        self._thread = threading.Thread(
            target=self._run, name="fastenhancer-grpc-worker", daemon=False
        )
        self._thread.start()

    @property
    def generation(self) -> int:
        return self._generation

    def _create_channel(self) -> grpc.Channel:
        options = (
            ("grpc.keepalive_time_ms", 20_000),
            ("grpc.keepalive_timeout_ms", 10_000),
            ("grpc.max_receive_message_length", 64 * 1024),
            ("grpc.max_send_message_length", 2 * 1024 * 1024),
        )
        if self._config.tls:
            credentials = grpc.ssl_channel_credentials(
                root_certificates=self._config.root_certificates,
                private_key=self._config.client_private_key,
                certificate_chain=self._config.client_certificate_chain,
            )
            return grpc.secure_channel(self._config.endpoint, credentials, options=options)
        return grpc.insecure_channel(self._config.endpoint, options=options)

    def try_send(self, start_sample: int, pcm_s16le: bytes) -> bool:
        samples = len(pcm_s16le) // 2
        with self._pending_lock:
            if (
                self._stop.is_set()
                or self._pending_samples + samples > self._config.max_request_queue_samples
            ):
                return False
            self._pending_samples += samples
        self._requests.put(AudioRequest(start_sample, pcm_s16le))
        return True

    def force_reset(self) -> None:
        self._reset.set()
        call = self._call
        if call is not None:
            call.cancel()
        self._drain_requests()
        self._requests.put_nowait(ResetRequest())

    def close(self, timeout_s: float = 1.0) -> None:
        with self._close_lock:
            self._close_locked(timeout_s)

    def _close_locked(self, timeout_s: float) -> None:
        if not self._stop.is_set():
            self._stop.set()
            self._requests.put_nowait(EndRequest())
        if not self._thread.is_alive():
            self._channel.close()
            return
        self._thread.join(timeout=timeout_s)
        if self._thread.is_alive():
            call = self._call
            if call is not None:
                call.cancel()
            self._channel.close()
            self._requests.put_nowait(StopRequest())
            self._thread.join(timeout=timeout_s)
        else:
            self._channel.close()
        if self._thread.is_alive():
            raise RuntimeError("gRPC worker did not stop")

    def _drain_requests(self) -> None:
        while True:
            try:
                self._requests.get_nowait()
            except queue.Empty:
                break
        with self._pending_lock:
            self._pending_samples = 0

    def _next_audio_or_stop(self) -> AudioRequest | None:
        while True:
            request = self._requests.get()
            if isinstance(request, AudioRequest):
                return request
            if isinstance(request, ResetRequest):
                continue
            if isinstance(request, EndRequest | StopRequest):
                return None

    def _request_iterator(self, first: AudioRequest, generation: int) -> Iterator[pb.ClientMessage]:
        metadata = {
            truncate_utf8(key, 64): truncate_utf8(value, 256)
            for key, value in list(self._metadata_provider().items())[:16]
            if key
        }
        yield pb.ClientMessage(
            start=pb.StartStream(
                protocol_version="1",
                stream_id=f"lk-{uuid.uuid4().hex}",
                input_start_sample=first.start_sample,
                sample_rate_hz=16_000,
                channels=1,
                sample_format=pb.SAMPLE_FORMAT_PCM_S16LE,
                metadata=metadata,
            )
        )
        sequence = 0
        expected_sample = first.start_sample
        request: Request = first
        while isinstance(request, AudioRequest):
            if request.start_sample != expected_sample or self._reset.is_set():
                return
            samples = len(request.pcm_s16le) // 2
            yield pb.ClientMessage(
                audio=pb.AudioChunk(
                    sequence=sequence,
                    input_start_sample=request.start_sample,
                    pcm_s16le=request.pcm_s16le,
                )
            )
            with self._pending_lock:
                self._pending_samples = max(0, self._pending_samples - samples)
            sequence += 1
            expected_sample += samples
            request = self._requests.get()
        if isinstance(request, EndRequest) and not self._reset.is_set():
            yield pb.ClientMessage(end=pb.EndStream(flush=request.flush))

    def _metadata(self) -> tuple[tuple[str, str], ...]:
        if self._config.api_key is None:
            return ()
        return (("authorization", f"Bearer {self._config.api_key}"),)

    def _validate_accepted(self, accepted: pb.StreamAccepted) -> None:
        expected = (
            accepted.protocol_version == "1"
            and accepted.model_name == EXPECTED_MODEL_NAME
            and accepted.model_revision == self._config.expected_model_revision
            and accepted.model_sha256 == self._config.expected_model_sha256
            and accepted.sample_rate_hz == 16_000
            and accepted.channels == 1
            and accepted.sample_format == pb.SAMPLE_FORMAT_PCM_S16LE
            and accepted.hop_samples == 256
            and accepted.algorithmic_delay_samples == 256
        )
        if not expected:
            raise ValueError("server capabilities do not match the locked FastEnhancer contract")

    def _run_generation(self, first: AudioRequest, generation: int) -> None:
        self._on_generation(generation, TransportState.CONNECTING)
        stub = pb_grpc.EnhancementServiceStub(self._channel)  # type: ignore[no-untyped-call]
        call = stub.Enhance(self._request_iterator(first, generation), metadata=self._metadata())
        self._call = call
        accepted = False
        output_sequence = 0
        expected_output = first.start_sample
        for response in call:
            body = response.WhichOneof("body")
            if not accepted:
                if body != "accepted":
                    raise ValueError("StreamAccepted must be the first server message")
                self._validate_accepted(response.accepted)
                accepted = True
                self._on_generation(generation, TransportState.ACTIVE)
                continue
            if body == "audio":
                audio = response.audio
                if audio.output_sequence != output_sequence:
                    raise ValueError("server output sequence is not contiguous")
                if audio.output_start_sample != expected_output:
                    raise ValueError("server output offset has a gap or overlap")
                if audio.valid_samples == 0 or len(audio.pcm_s16le) != audio.valid_samples * 2:
                    raise ValueError("server output byte count is invalid")
                self._on_audio(generation, audio.output_start_sample, bytes(audio.pcm_s16le))
                output_sequence += 1
                expected_output += audio.valid_samples
                continue
            if body == "ended":
                return
            raise ValueError("unexpected server response")
        if not self._stop.is_set():
            raise ConnectionError("server response stream ended without StreamEnded")

    def _run(self) -> None:
        backoff = self._config.reconnect_min_delay_s
        logger = logging.getLogger(__name__)
        last_warning = 0.0
        while True:
            self._on_generation(self._generation, TransportState.CONNECTING)
            try:
                self._wait_until_ready()
            except ValueError as exc:
                self._on_protocol_error(self._generation, str(exc))
                if self._stop.is_set():
                    break
                self._on_generation(self._generation, TransportState.BACKOFF)
                self._stop.wait(backoff)
                backoff = min(backoff * 2.0, self._config.reconnect_max_delay_s)
                continue
            except (
                grpc.FutureCancelledError,
                grpc.FutureTimeoutError,
                grpc.RpcError,
                ConnectionError,
            ):
                if self._stop.is_set():
                    break
                self._on_generation(self._generation, TransportState.BACKOFF)
                self._stop.wait(backoff)
                backoff = min(backoff * 2.0, self._config.reconnect_max_delay_s)
                continue
            first = self._next_audio_or_stop()
            if first is None:
                break
            self._generation += 1
            generation = self._generation
            self._reset.clear()
            try:
                self._run_generation(first, generation)
                backoff = self._config.reconnect_min_delay_s
            except ValueError as exc:
                self._on_protocol_error(generation, str(exc))
                self._drain_requests()
            except (grpc.RpcError, grpc.FutureTimeoutError, ConnectionError):
                now = time.monotonic()
                if not self._stop.is_set() and now - last_warning >= 5.0:
                    logger.warning("FastEnhancer transport unavailable")
                    last_warning = now
                self._drain_requests()
            finally:
                self._call = None
            if self._stop.is_set():
                break
            self._on_generation(generation, TransportState.BACKOFF)
            deadline = time.monotonic() + backoff * random.uniform(0.8, 1.2)  # noqa: S311
            while not self._stop.is_set() and time.monotonic() < deadline:
                self._stop.wait(min(0.05, deadline - time.monotonic()))
            backoff = min(backoff * 2.0, self._config.reconnect_max_delay_s)
        self._on_generation(self._generation, TransportState.CLOSED)

    def _wait_until_ready(self) -> None:
        stub = pb_grpc.EnhancementServiceStub(  # type: ignore[no-untyped-call]
            self._channel
        )
        future = stub.GetCapabilities.future(
            pb.GetCapabilitiesRequest(),
            timeout=self._config.connect_timeout_s,
            metadata=self._metadata(),
        )
        self._call = future
        try:
            response = future.result()
        finally:
            if self._call is future:
                self._call = None
        self._validate_accepted(response.capabilities)
