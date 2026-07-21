"""LiveKit FrameProcessor with offset-exact remote enhancement fallback."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from livekit import rtc

from .metrics import PluginMetrics
from .timeline import ContiguousPCM, EnhancedSegments
from .transport import (
    EXPECTED_CHECKPOINT_SHA256,
    EXPECTED_REVISION,
    GRPCWorker,
    TransportConfig,
    TransportState,
    truncate_utf8,
)

SAMPLE_RATE_HZ = 16_000
ALGORITHMIC_DELAY_SAMPLES = 256


@dataclass(frozen=True, slots=True)
class RemoteFastEnhancerConfig:
    endpoint: str
    api_key: str | None = field(default=None, repr=False)
    tls: bool = True
    root_certificates: bytes | None = None
    client_certificate_chain: bytes | None = None
    client_private_key: bytes | None = field(default=None, repr=False)
    connect_timeout_s: float = 2.0
    response_wait_ms: float = 12.0
    max_buffer_ms: int = 500
    max_request_queue_ms: int = 500
    reconnect_min_delay_s: float = 0.1
    reconnect_max_delay_s: float = 2.0
    stream_metadata: Mapping[str, str] | None = None
    expected_model_revision: str = EXPECTED_REVISION
    expected_model_sha256: str = EXPECTED_CHECKPOINT_SHA256

    def __post_init__(self) -> None:
        if not self.endpoint:
            raise ValueError("endpoint must not be empty")
        if self.connect_timeout_s <= 0 or self.response_wait_ms < 0:
            raise ValueError("timeouts must be positive")
        if self.max_buffer_ms < 32 or self.max_request_queue_ms < 16:
            raise ValueError("buffer bounds are too small for the model contract")
        if not 0 < self.reconnect_min_delay_s <= self.reconnect_max_delay_s:
            raise ValueError("invalid reconnect delay range")
        if bool(self.client_certificate_chain) != bool(self.client_private_key):
            raise ValueError("client certificate chain and private key must be configured together")
        if (self.client_certificate_chain or self.client_private_key) and not self.tls:
            raise ValueError("client certificates require TLS")
        if len(self.expected_model_revision) != 40:
            raise ValueError("expected_model_revision must be a full commit hash")
        if len(self.expected_model_sha256) != 64:
            raise ValueError("expected_model_sha256 must be a full SHA-256")
        metadata = self.stream_metadata or {}
        if len(metadata) > 16:
            raise ValueError("stream_metadata has more than 16 entries")
        for key, value in metadata.items():
            if not key or len(str(key).encode()) > 64 or len(str(value).encode()) > 256:
                raise ValueError("stream_metadata entry exceeds protocol limits")


class RemoteFastEnhancer(rtc.FrameProcessor[rtc.AudioFrame]):
    """A single-track processor. Instances must never be shared between tracks."""

    def __init__(
        self,
        endpoint: str,
        api_key: str | None = None,
        tls: bool = True,
        root_certificates: bytes | None = None,
        client_certificate_chain: bytes | None = None,
        client_private_key: bytes | None = None,
        connect_timeout_s: float = 2.0,
        response_wait_ms: float = 12.0,
        max_buffer_ms: int = 500,
        reconnect_min_delay_s: float = 0.1,
        reconnect_max_delay_s: float = 2.0,
        stream_metadata: Mapping[str, str] | None = None,
        *,
        max_request_queue_ms: int = 500,
        expected_model_revision: str = EXPECTED_REVISION,
        expected_model_sha256: str = EXPECTED_CHECKPOINT_SHA256,
    ) -> None:
        self.config = RemoteFastEnhancerConfig(
            endpoint=endpoint,
            api_key=api_key,
            tls=tls,
            root_certificates=root_certificates,
            client_certificate_chain=client_certificate_chain,
            client_private_key=client_private_key,
            connect_timeout_s=connect_timeout_s,
            response_wait_ms=response_wait_ms,
            max_buffer_ms=max_buffer_ms,
            max_request_queue_ms=max_request_queue_ms,
            reconnect_min_delay_s=reconnect_min_delay_s,
            reconnect_max_delay_s=reconnect_max_delay_s,
            stream_metadata=stream_metadata,
            expected_model_revision=expected_model_revision,
            expected_model_sha256=expected_model_sha256,
        )
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._enabled = True
        self._closed = False
        self._input_cursor = 0
        self._output_cursor = 0
        self._raw = ContiguousPCM(0)
        self._enhanced = EnhancedSegments()
        self._remote_generation = 0
        self._transport_state = TransportState.DISCONNECTED
        self._metadata = dict(stream_metadata or {})
        self._metrics = PluginMetrics()
        transport_config = TransportConfig(
            endpoint=endpoint,
            api_key=api_key,
            tls=tls,
            root_certificates=root_certificates,
            client_certificate_chain=client_certificate_chain,
            client_private_key=client_private_key,
            connect_timeout_s=connect_timeout_s,
            reconnect_min_delay_s=reconnect_min_delay_s,
            reconnect_max_delay_s=reconnect_max_delay_s,
            max_request_queue_samples=max_request_queue_ms * SAMPLE_RATE_HZ // 1000,
            expected_model_revision=expected_model_revision,
            expected_model_sha256=expected_model_sha256,
        )
        self._worker = GRPCWorker(
            transport_config,
            metadata_provider=self._metadata_snapshot,
            on_audio=self._receive_audio,
            on_generation=self._generation_changed,
            on_protocol_error=self._protocol_error,
        )

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        reset = False
        with self._condition:
            if self._closed:
                return
            value = bool(value)
            if value == self._enabled:
                return
            self._enabled = value
            self._enhanced.clear()
            self._raw.clear_at(self._input_cursor)
            self._output_cursor = self._input_cursor
            reset = True
            self._condition.notify_all()
        if reset:
            self._worker.force_reset()

    @property
    def metrics(self) -> dict[str, int]:
        with self._lock:
            return self._metrics.snapshot()

    @property
    def transport_state(self) -> str:
        with self._lock:
            return self._transport_state.value

    def _metadata_snapshot(self) -> Mapping[str, str]:
        with self._lock:
            return dict(self._metadata)

    def _on_stream_info_updated(
        self, *, room_name: str, participant_identity: str, publication_sid: str
    ) -> None:
        with self._lock:
            self._metadata.update(
                {
                    "room_name": truncate_utf8(room_name, 256),
                    "participant_identity": truncate_utf8(participant_identity, 256),
                    "publication_sid": truncate_utf8(publication_sid, 256),
                }
            )

    def _on_stream_info_cleared(self) -> None:
        with self._lock:
            for key in ("room_name", "participant_identity", "publication_sid"):
                self._metadata.pop(key, None)

    def _generation_changed(self, generation: int, state: TransportState) -> None:
        with self._condition:
            if generation > self._remote_generation:
                if self._remote_generation:
                    self._metrics.reconnects += 1
                self._remote_generation = generation
                self._enhanced.clear()
            self._transport_state = state
            self._condition.notify_all()

    def _protocol_error(self, generation: int, detail: str) -> None:
        del detail
        with self._condition:
            if generation >= self._remote_generation:
                self._metrics.protocol_mismatches += 1
                self._enhanced.clear()
            self._condition.notify_all()

    def _receive_audio(self, generation: int, start_sample: int, pcm_s16le: bytes) -> None:
        reset = False
        with self._condition:
            samples = len(pcm_s16le) // 2
            if generation != self._remote_generation:
                self._metrics.late_response_samples += samples
                return
            end_sample = start_sample + samples
            if end_sample <= self._output_cursor:
                self._metrics.late_response_samples += samples
                return
            if start_sample < self._output_cursor:
                trim = self._output_cursor - start_sample
                self._metrics.late_response_samples += trim
                pcm_s16le = pcm_s16le[trim * 2 :]
                start_sample = self._output_cursor
                end_sample = start_sample + len(pcm_s16le) // 2
            if end_sample > self._input_cursor:
                self._metrics.protocol_mismatches += 1
                reset = True
            else:
                try:
                    self._enhanced.insert(start_sample, pcm_s16le)
                except ValueError:
                    self._metrics.protocol_mismatches += 1
                    self._enhanced.clear()
                    reset = True
            self._condition.notify_all()
        if reset:
            self._worker.force_reset()

    @staticmethod
    def _frame_bytes(frame: rtc.AudioFrame) -> bytes:
        return frame.data.cast("B").tobytes()

    def _process(self, frame: rtc.AudioFrame) -> rtc.AudioFrame:
        if frame.sample_rate != SAMPLE_RATE_HZ or frame.num_channels != 1:
            raise ValueError("RemoteFastEnhancer accepts only 16000 Hz mono AudioFrame input")
        pcm = self._frame_bytes(frame)
        sample_count = frame.samples_per_channel
        if len(pcm) != sample_count * 2:
            raise ValueError("AudioFrame PCM length does not match samples_per_channel")
        if sample_count > self.config.max_buffer_ms * SAMPLE_RATE_HZ // 1000:
            raise ValueError("AudioFrame exceeds configured bounded timeline capacity")
        with self._condition:
            if self._closed:
                raise RuntimeError("RemoteFastEnhancer is closed")
            self._metrics.frames_in += 1
            self._metrics.samples_in += sample_count
            start_sample = self._input_cursor
            self._input_cursor += sample_count
            if not self._enabled:
                self._output_cursor = self._input_cursor
                self._metrics.frames_out += 1
                self._metrics.samples_out += sample_count
                self._metrics.raw_fallback_samples += sample_count
                return frame
            self._raw.append(start_sample, pcm)
        if not self._worker.try_send(start_sample, pcm):
            with self._condition:
                self._metrics.queue_overflows += 1
                self._enhanced.clear()
            self._worker.force_reset()
        with self._condition:
            eligible_until = max(0, self._input_cursor - ALGORITHMIC_DELAY_SAMPLES)
            output_samples = min(sample_count, max(0, eligible_until - self._output_cursor))
            output_start = self._output_cursor
            deadline = time.monotonic() + self.config.response_wait_ms / 1000.0
            enhanced = self._enhanced.read(output_start, output_samples) if output_samples else b""
            while output_samples and enhanced is None and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
                enhanced = self._enhanced.read(output_start, output_samples)
            if enhanced is None:
                output = self._raw.read(output_start, output_samples)
                self._metrics.raw_fallback_samples += output_samples
            else:
                output = enhanced
                self._metrics.enhanced_samples += output_samples
            self._output_cursor += output_samples
            self._raw.discard_before(self._output_cursor)
            self._enhanced.discard_before(self._output_cursor)
            self._metrics.frames_out += 1
            self._metrics.samples_out += output_samples
        return rtc.AudioFrame(
            data=output,
            sample_rate=SAMPLE_RATE_HZ,
            num_channels=1,
            samples_per_channel=output_samples,
            userdata=frame.userdata,
        )

    def _close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()
        self._worker.close()
        with self._condition:
            self._enhanced.clear()
            self._raw.clear_at(self._input_cursor)


def audio_enhancement(
    endpoint: str,
    api_key: str | None = None,
    **kwargs: object,
) -> RemoteFastEnhancer:
    """Create a track-local RemoteFastEnhancer instance."""
    return RemoteFastEnhancer(endpoint=endpoint, api_key=api_key, **kwargs)  # type: ignore[arg-type]
