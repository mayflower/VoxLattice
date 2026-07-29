from __future__ import annotations

import array
import socket
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import grpc
import pytest
from fastenhancer.v1 import enhancement_pb2 as pb
from fastenhancer.v1 import enhancement_pb2_grpc as pb_grpc
from livekit import rtc
from livekit.plugins.fastenhancer import RemoteFastEnhancer
from livekit.plugins.fastenhancer.transport import truncate_utf8


def negate_pcm(value: bytes) -> bytes:
    samples = array.array("h")
    samples.frombytes(value)
    return array.array("h", [max(-32768, min(32767, -sample)) for sample in samples]).tobytes()


@dataclass(slots=True)
class SyncEnhancer(pb_grpc.EnhancementServiceServicer):
    delay_s: float = 0.0

    @staticmethod
    def accepted() -> pb.StreamAccepted:
        return pb.StreamAccepted(
            protocol_version="1",
            model_name="FastEnhancer-B",
            model_revision="49ab55a57e3a064d94c6412cd0f0a383a55ca0f8",
            model_sha256="980ec00d9c3cb0497893c815c718a2fe44970329ae8477d22596d0a1373f2382",
            sample_rate_hz=16_000,
            channels=1,
            sample_format=pb.SAMPLE_FORMAT_PCM_S16LE,
            hop_samples=256,
            algorithmic_delay_samples=256,
            cuda_device="test-double",
            max_audio_chunk_samples=16_000,
        )

    def Enhance(
        self, request_iterator: Iterator[pb.ClientMessage], context: grpc.ServicerContext
    ) -> Iterator[pb.ServerMessage]:
        metadata = dict(context.invocation_metadata())
        if metadata.get("authorization") != "Bearer plugin-test-token":
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "authentication required")
        first = next(request_iterator)
        assert first.WhichOneof("body") == "start"
        base = first.start.input_start_sample
        yield pb.ServerMessage(accepted=self.accepted())
        buffered = bytearray()
        pending: tuple[int, bytes, int] | None = None
        input_samples = 0
        output_samples = 0
        output_sequence = 0

        def process_hop(start: int, pcm: bytes, valid: int) -> pb.ServerMessage | None:
            nonlocal pending, output_samples, output_sequence
            previous = pending
            pending = (start, pcm, valid)
            if previous is None:
                return None
            if self.delay_s:
                time.sleep(self.delay_s)
            message = pb.ServerMessage(
                audio=pb.EnhancedAudio(
                    output_sequence=output_sequence,
                    output_start_sample=previous[0],
                    pcm_s16le=negate_pcm(previous[1][: previous[2] * 2]),
                    valid_samples=previous[2],
                )
            )
            output_sequence += 1
            output_samples += previous[2]
            return message

        cursor = base
        for request in request_iterator:
            body = request.WhichOneof("body")
            if body == "audio":
                buffered.extend(request.audio.pcm_s16le)
                input_samples += len(request.audio.pcm_s16le) // 2
                while len(buffered) >= 512:
                    hop = bytes(buffered[:512])
                    del buffered[:512]
                    response = process_hop(cursor, hop, 256)
                    cursor += 256
                    if response is not None:
                        yield response
            elif body == "end":
                if buffered:
                    valid = len(buffered) // 2
                    hop = bytes(buffered) + bytes(512 - len(buffered))
                    response = process_hop(cursor, hop, valid)
                    if response is not None:
                        yield response
                    buffered.clear()
                if pending is not None:
                    previous = pending
                    if self.delay_s:
                        time.sleep(self.delay_s)
                    yield pb.ServerMessage(
                        audio=pb.EnhancedAudio(
                            output_sequence=output_sequence,
                            output_start_sample=previous[0],
                            pcm_s16le=negate_pcm(previous[1][: previous[2] * 2]),
                            valid_samples=previous[2],
                        )
                    )
                    output_samples += previous[2]
                yield pb.ServerMessage(
                    ended=pb.StreamEnded(
                        input_samples=input_samples, output_samples=output_samples, flushed=True
                    )
                )
                return

    def GetCapabilities(
        self, request: pb.GetCapabilitiesRequest, context: grpc.ServicerContext
    ) -> pb.GetCapabilitiesResponse:
        del request, context
        return pb.GetCapabilitiesResponse(capabilities=self.accepted(), max_active_streams=8)


@pytest.fixture
def sync_server() -> Iterator[str]:
    server = grpc.server(ThreadPoolExecutor(max_workers=8))
    pb_grpc.add_EnhancementServiceServicer_to_server(SyncEnhancer(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        yield f"127.0.0.1:{port}"
    finally:
        server.stop(0).wait()


def settle(processor: RemoteFastEnhancer, samples: int = 1, timeout_s: float = 2.0) -> bool:
    """Wait until the next `samples` of enhanced audio have arrived.

    _process never waits for the server — it runs on the agent's event loop.
    In production the gap between two frames (10-50ms) is the budget the answer
    actually gets. These tests push frames back to back, so without this the
    enhanced audio has had no wall-clock time to arrive at all.
    """
    deadline = time.monotonic() + timeout_s
    with processor._condition:
        while time.monotonic() < deadline:
            if processor._enhanced.read(processor._output_cursor, samples) is not None:
                return True
            processor._condition.wait(0.01)
    return False


def frame(samples: list[int], userdata: dict[str, object] | None = None) -> rtc.AudioFrame:
    return rtc.AudioFrame(
        array.array("h", samples).tobytes(),
        sample_rate=16_000,
        num_channels=1,
        samples_per_channel=len(samples),
        userdata=userdata,
    )


@pytest.mark.integration
def test_frame_processor_enhanced_alignment_and_close(sync_server: str) -> None:
    before = {thread.name for thread in threading.enumerate()}
    processor = RemoteFastEnhancer(
        sync_server,
        api_key="plugin-test-token",
        tls=False,
        output_delay_ms=32,
    )
    userdata = {"source": "test"}
    first_samples = list(range(512))
    second_samples = list(range(1000, 1512))
    third_samples = list(range(2000, 2512))

    # The output trails the input by the 256-sample model delay plus the
    # 512-sample (32ms) response budget, so the first frame carries nothing.
    assert processor._process(frame(first_samples)).samples_per_channel == 0
    assert settle(processor, 256)

    second = processor._process(frame(second_samples, userdata))
    assert second.samples_per_channel == 256
    assert list(second.data) == [-sample for sample in first_samples[:256]]
    assert second.userdata is userdata
    assert settle(processor, 512)

    third = processor._process(frame(third_samples))
    assert third.samples_per_channel == 512
    assert list(third.data[:256]) == [-sample for sample in first_samples[256:]]
    assert list(third.data[256:]) == [-sample for sample in second_samples[:256]]
    assert processor.metrics["raw_fallback_samples"] == 0
    processor._close()
    processor._close()
    after = {thread.name for thread in threading.enumerate()}
    assert "fastenhancer-grpc-worker" not in after - before


@pytest.mark.integration
def test_variable_first_output_and_raw_fallback(sync_server: str) -> None:
    processor = RemoteFastEnhancer(
        sync_server,
        api_key="plugin-test-token",
        tls=False,
        output_delay_ms=0,
    )
    first = processor._process(frame([7] * 160))
    assert first.samples_per_channel == 0
    second = processor._process(frame([9] * 160))
    assert second.samples_per_channel == 64
    assert list(second.data) == [7] * 64
    assert processor.metrics["raw_fallback_samples"] == 64
    processor.enabled = False
    passthrough = frame([11] * 320)
    assert processor._process(passthrough) is passthrough
    processor.enabled = True
    processor._close()


@pytest.mark.integration
@pytest.mark.parametrize("frame_ms", [10, 16, 20, 32, 50])
def test_all_supported_livekit_frame_sizes(sync_server: str, frame_ms: int) -> None:
    samples = frame_ms * 16
    processor = RemoteFastEnhancer(
        sync_server,
        api_key="plugin-test-token",
        tls=False,
        output_delay_ms=0,
    )
    try:
        first = processor._process(frame([100] * samples))
        assert first.samples_per_channel == max(0, samples - 256)
        second = processor._process(frame([200] * samples))
        assert 0 < second.samples_per_channel <= samples
        assert processor.metrics["samples_out"] == min(samples * 2 - 256, samples * 2)
    finally:
        processor._close()


def test_wrong_audio_contract_fails_before_network(sync_server: str) -> None:
    processor = RemoteFastEnhancer(sync_server, api_key="plugin-test-token", tls=False)
    wrong = rtc.AudioFrame(bytes(960), sample_rate=48_000, num_channels=1, samples_per_channel=480)
    with pytest.raises(ValueError, match="16000 Hz mono"):
        processor._process(wrong)
    processor._close()


@pytest.mark.integration
def test_silence_does_not_replace_final_delayed_speech(sync_server: str) -> None:
    processor = RemoteFastEnhancer(
        sync_server,
        api_key="plugin-test-token",
        tls=False,
        output_delay_ms=16,
    )
    try:
        speech = list(range(1, 513))
        assert processor._process(frame(speech)).samples_per_channel == 0
        assert settle(processor, 256)
        # The speech still owed from the first frame must survive the silence
        # that follows it, rather than being overwritten by it.
        first_half = processor._process(frame([0] * 256))
        assert list(first_half.data) == [-sample for sample in speech[:256]]
        assert settle(processor, 256)
        second_half = processor._process(frame([0] * 256))
        assert list(second_half.data) == [-sample for sample in speech[256:]]
    finally:
        processor._close()


def test_utf8_metadata_truncation_is_byte_bounded_and_valid() -> None:
    truncated = truncate_utf8("é" * 256, 255)
    assert len(truncated.encode("utf-8")) <= 255
    assert truncated == "é" * 127


def test_process_does_not_block_while_server_unreachable() -> None:
    # Nothing listens on this port, so after the first refused connect the
    # worker sits in BACKOFF and never delivers. _process must not stall the
    # caller (it runs on the agent's event loop): it falls back to raw.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()

    processor = RemoteFastEnhancer(
        f"127.0.0.1:{port}",
        api_key="plugin-test-token",
        tls=False,
        output_delay_ms=12,
    )
    try:
        # Wait until the worker has settled into the reconnect backoff loop, so
        # the timed frame below is measured against a definitively-down server.
        deadline = time.monotonic() + 3
        while processor.transport_state != "backoff" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert processor.transport_state == "backoff"

        # The output trails the input by the 256-sample model delay plus the
        # 192-sample (12ms) response budget, so the first two frames are still
        # inside it and the third is the first to carry a full frame.
        processor._process(frame([1] * 256))
        processor._process(frame([2] * 256))
        started = time.monotonic()
        output = processor._process(frame([3] * 256))
        elapsed = time.monotonic() - started
        assert elapsed < 0.2, f"blocked {elapsed:.3f}s despite unreachable server"
        assert output.samples_per_channel == 256
        assert processor.metrics["raw_fallback_samples"] == 320
        assert processor.metrics["enhanced_samples"] == 0
    finally:
        processor._close()


def test_close_never_raises_into_the_event_loop() -> None:
    """LiveKit closes an owned processor synchronously from the room's track
    handlers, so a worker that refuses to stop must not break a track relink or
    session shutdown — the same slow/unreachable server the raw fallback exists
    to tolerate is exactly when the join times out."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()

    processor = RemoteFastEnhancer(
        f"127.0.0.1:{port}",
        api_key="plugin-test-token",
        tls=False,
    )

    class StuckWorker:
        closed = False

        def close(self) -> None:
            type(self).closed = True
            raise RuntimeError("gRPC worker did not stop")

    real_worker = processor._worker
    processor._worker = StuckWorker()  # type: ignore[assignment]
    try:
        processor._close()  # must not raise
        assert StuckWorker.closed
    finally:
        real_worker.close()


def test_process_does_not_block_while_server_is_reachable_but_silent() -> None:
    # The unreachable case above short-circuits on the BACKOFF transport state.
    # This is the case that state cannot catch: the connection is accepted, so
    # the worker is CONNECTING/CONNECTED and enhanced audio is genuinely "on its
    # way" — which is exactly when waiting for it looks reasonable and is not.
    # _process runs inline on the agent's event loop, so a per-frame wait here
    # stalls TTS playout and barge-in for every frame of the call.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(2)
    release = threading.Event()

    def hold_connection() -> None:
        try:
            connection, _ = listener.accept()
            with connection:
                release.wait(5)
        except OSError:
            pass
        finally:
            listener.close()

    holder = threading.Thread(target=hold_connection, daemon=True)
    holder.start()
    processor = RemoteFastEnhancer(
        f"127.0.0.1:{listener.getsockname()[1]}",
        api_key="plugin-test-token",
        tls=False,
        connect_timeout_s=5,
        output_delay_ms=200,
    )
    try:
        assert processor.transport_state not in {"backoff", "closed"}
        # 200ms budget over five frames: the old wait-for-the-answer loop spent
        # it on every frame, so this loop took ~1s. It must now be immediate.
        started = time.monotonic()
        for value in range(5):
            processor._process(frame([value] * 256))
        elapsed = time.monotonic() - started
        assert elapsed < 0.1, f"blocked {elapsed:.3f}s on a reachable but silent server"
    finally:
        release.set()
        processor._close()
        holder.join(3)


def test_close_cancels_stalled_readiness_rpc() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(2)
    accepted = threading.Event()
    release = threading.Event()

    def hold_connection() -> None:
        try:
            connection, _ = listener.accept()
            with connection:
                accepted.set()
                release.wait(3)
        finally:
            listener.close()

    holder = threading.Thread(target=hold_connection, daemon=True)
    holder.start()
    processor = RemoteFastEnhancer(
        f"127.0.0.1:{listener.getsockname()[1]}",
        api_key="plugin-test-token",
        tls=False,
        connect_timeout_s=5,
        output_delay_ms=0,
    )
    try:
        assert accepted.wait(2)
        started = time.monotonic()
        processor._close()
        assert time.monotonic() - started < 2.5
        assert not processor._worker._thread.is_alive()
    finally:
        release.set()
        holder.join(3)


@pytest.mark.integration
def test_capability_mismatch_falls_back_and_closes(sync_server: str) -> None:
    processor = RemoteFastEnhancer(
        sync_server,
        api_key="plugin-test-token",
        tls=False,
        output_delay_ms=0,
        expected_model_sha256="0" * 64,
    )
    output = processor._process(frame([17] * 512))
    assert list(output.data) == [17] * 256
    deadline = time.monotonic() + 1
    while processor.metrics["protocol_mismatches"] == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert processor.metrics["protocol_mismatches"] >= 1
    processor._close()


def test_bounded_request_overflow_is_visible() -> None:
    processor = RemoteFastEnhancer(
        "127.0.0.1:1",
        api_key="plugin-test-token",
        tls=False,
        connect_timeout_s=0.05,
        output_delay_ms=0,
        max_request_queue_ms=16,
        reconnect_min_delay_s=0.01,
        reconnect_max_delay_s=0.02,
    )
    output = processor._process(frame([3] * 512))
    assert list(output.data) == [3] * 256
    assert processor.metrics["queue_overflows"] == 1
    processor._close()


@pytest.mark.integration
def test_reconnect_uses_new_generation_without_timeline_mix() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    processor = RemoteFastEnhancer(
        f"127.0.0.1:{port}",
        api_key="plugin-test-token",
        tls=False,
        connect_timeout_s=0.05,
        output_delay_ms=20,
        reconnect_min_delay_s=0.01,
        reconnect_max_delay_s=0.02,
    )
    # Still inside the model delay plus the 320-sample (20ms) response budget.
    assert processor._process(frame([21] * 512)).samples_per_channel == 0
    # Raw, because nothing is listening yet.
    assert list(processor._process(frame([21] * 256)).data) == [21] * 192
    server = grpc.server(ThreadPoolExecutor(max_workers=4))
    pb_grpc.add_EnhancementServiceServicer_to_server(SyncEnhancer(), server)
    assert server.add_insecure_port(f"127.0.0.1:{port}") == port
    server.start()
    try:
        deadline = time.monotonic() + 2
        while processor.metrics["enhanced_samples"] == 0 and time.monotonic() < deadline:
            processor._process(frame([22] * 256))
            time.sleep(0.02)
        assert processor.metrics["enhanced_samples"] > 0
        assert processor.metrics["protocol_mismatches"] == 0
    finally:
        processor._close()
        server.stop(0).wait()
