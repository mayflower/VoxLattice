from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import grpc
import pytest
from fastenhancer.v1 import enhancement_pb2 as pb
from fastenhancer.v1 import enhancement_pb2_grpc as pb_grpc
from fastenhancer_server.metrics import ServerMetrics
from fastenhancer_server.scheduler import BatchScheduler
from fastenhancer_server.service import EnhancementService
from fastenhancer_server.settings import ServerSettings

from .fakes import IdentityDelayModel


def settings(token: str) -> ServerSettings:
    return ServerSettings(
        grpc_host="127.0.0.1",
        grpc_port=0,
        http_host="127.0.0.1",
        http_port=0,
        checkpoint_path=Path("unused"),
        manifest_path=Path("unused"),
        model_config_path=Path("unused"),
        cuda_device="cuda:0",
        required_device_name="NVIDIA RTX A6000",
        api_token=token,
        allow_insecure=True,
        tls_certificate=None,
        tls_private_key=None,
        tls_client_ca=None,
        max_active_streams=32,
        max_batch_size=8,
        max_batch_wait_ms=1,
        max_pending_audio_ms_per_stream=500,
        max_output_audio_ms_per_stream=500,
        stream_idle_timeout_s=2,
        max_audio_chunk_samples=16_000,
        graceful_shutdown_s=1,
    )


async def messages(values: list[pb.ClientMessage]) -> AsyncIterator[pb.ClientMessage]:
    for value in values:
        yield value


async def start_server(
    *, idle_timeout_s: float | None = None, max_active_streams: int | None = None
) -> tuple[grpc.aio.Server, BatchScheduler, int, str]:
    token = "test-token-at-least-16"  # noqa: S105 -- local test credential
    config = settings(token)
    if idle_timeout_s is not None:
        from dataclasses import replace

        config = replace(config, stream_idle_timeout_s=idle_timeout_s)
    if max_active_streams is not None:
        from dataclasses import replace

        config = replace(config, max_active_streams=max_active_streams)
    metrics = ServerMetrics()
    scheduler = BatchScheduler(
        IdentityDelayModel(),
        metrics,
        max_active_streams=config.max_active_streams,
        max_batch_size=config.max_batch_size,
        max_batch_wait_ms=config.max_batch_wait_ms,
    )
    scheduler.start()
    server = grpc.aio.server()
    pb_grpc.add_EnhancementServiceServicer_to_server(
        EnhancementService(
            model=IdentityDelayModel(), scheduler=scheduler, settings=config, metrics=metrics
        ),
        server,
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    return server, scheduler, port, token


def start(stream_id: str = "stream-1", offset: int = 100) -> pb.ClientMessage:
    return pb.ClientMessage(
        start=pb.StartStream(
            protocol_version="1",
            stream_id=stream_id,
            input_start_sample=offset,
            sample_rate_hz=16_000,
            channels=1,
            sample_format=pb.SAMPLE_FORMAT_PCM_S16LE,
        )
    )


@pytest.mark.integration
async def test_flush_preserves_offsets_and_sample_count() -> None:
    server, scheduler, port, token = await start_server()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = pb_grpc.EnhancementServiceStub(channel)
            pcm = b"\x01\x00" * 600
            responses = [
                response
                async for response in stub.Enhance(
                    messages(
                        [
                            start(),
                            pb.ClientMessage(
                                audio=pb.AudioChunk(
                                    sequence=0, input_start_sample=100, pcm_s16le=pcm
                                )
                            ),
                            pb.ClientMessage(end=pb.EndStream(flush=True)),
                        ]
                    ),
                    metadata=(("authorization", f"Bearer {token}"),),
                )
            ]
        assert responses[0].WhichOneof("body") == "accepted"
        audio = [response.audio for response in responses if response.WhichOneof("body") == "audio"]
        assert [value.output_start_sample for value in audio] == [100, 356, 612]
        assert [value.valid_samples for value in audio] == [256, 256, 88]
        assert b"".join(value.pcm_s16le for value in audio) == pcm
        assert responses[-1].ended.input_samples == 600
        assert responses[-1].ended.output_samples == 600
        assert responses[-1].ended.flushed
    finally:
        await server.stop(0)
        await scheduler.close()


@pytest.mark.integration
async def test_protocol_and_auth_failures_use_grpc_status() -> None:
    server, scheduler, port, token = await start_server()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = pb_grpc.EnhancementServiceStub(channel)
            with pytest.raises(grpc.aio.AioRpcError) as unauthenticated:
                await stub.GetCapabilities(pb.GetCapabilitiesRequest())
            assert unauthenticated.value.code() == grpc.StatusCode.UNAUTHENTICATED
            call = stub.Enhance(
                messages([pb.ClientMessage(end=pb.EndStream(flush=True))]),
                metadata=(("authorization", f"Bearer {token}"),),
            )
            with pytest.raises(grpc.aio.AioRpcError) as invalid:
                async for _ in call:
                    continue
            assert invalid.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    finally:
        await server.stop(0)
        await scheduler.close()


@pytest.mark.integration
@pytest.mark.parametrize("stream_count", [1, 8, 32])
async def test_parallel_streams_are_isolated(stream_count: int) -> None:
    server, scheduler, port, token = await start_server()

    async def run_one(index: int) -> bytes:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = pb_grpc.EnhancementServiceStub(channel)
            pcm = int(index + 1).to_bytes(2, "little", signed=True) * 512
            responses = [
                response
                async for response in stub.Enhance(
                    messages(
                        [
                            start(f"stream-{index}", index * 1000),
                            pb.ClientMessage(
                                audio=pb.AudioChunk(
                                    sequence=0,
                                    input_start_sample=index * 1000,
                                    pcm_s16le=pcm,
                                )
                            ),
                            pb.ClientMessage(end=pb.EndStream(flush=True)),
                        ]
                    ),
                    metadata=(("authorization", f"Bearer {token}"),),
                )
            ]
            return b"".join(
                response.audio.pcm_s16le
                for response in responses
                if response.WhichOneof("body") == "audio"
            )

    try:
        import asyncio

        outputs = await asyncio.gather(*(run_one(index) for index in range(stream_count)))
        for index, output in enumerate(outputs):
            expected = int(index + 1).to_bytes(2, "little", signed=True) * 512
            assert output == expected
    finally:
        await server.stop(0)
        await scheduler.close()


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_message",
    [
        pb.ClientMessage(
            audio=pb.AudioChunk(sequence=1, input_start_sample=100, pcm_s16le=b"\x00\x00")
        ),
        pb.ClientMessage(
            audio=pb.AudioChunk(sequence=0, input_start_sample=101, pcm_s16le=b"\x00\x00")
        ),
        start("duplicate", 100),
    ],
)
async def test_sequence_offset_and_duplicate_start_fail(
    invalid_message: pb.ClientMessage,
) -> None:
    server, scheduler, port, token = await start_server()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            call = pb_grpc.EnhancementServiceStub(channel).Enhance(
                messages([start("duplicate", 100), invalid_message]),
                metadata=(("authorization", f"Bearer {token}"),),
            )
            with pytest.raises(grpc.aio.AioRpcError) as failure:
                async for _ in call:
                    continue
            assert failure.value.code() in {
                grpc.StatusCode.INVALID_ARGUMENT,
                grpc.StatusCode.FAILED_PRECONDITION,
            }
    finally:
        await server.stop(0)
        await scheduler.close()


@pytest.mark.integration
async def test_empty_stream_flushes_zero_samples() -> None:
    server, scheduler, port, token = await start_server()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            responses = [
                response
                async for response in pb_grpc.EnhancementServiceStub(channel).Enhance(
                    messages([start(), pb.ClientMessage(end=pb.EndStream(flush=True))]),
                    metadata=(("authorization", f"Bearer {token}"),),
                )
            ]
        assert [response.WhichOneof("body") for response in responses] == ["accepted", "ended"]
        assert responses[-1].ended.input_samples == 0
        assert responses[-1].ended.output_samples == 0
    finally:
        await server.stop(0)
        await scheduler.close()


@pytest.mark.integration
async def test_idle_stream_is_terminated() -> None:
    server, scheduler, port, token = await start_server(idle_timeout_s=0.05)

    async def idle_messages() -> AsyncIterator[pb.ClientMessage]:
        import asyncio

        yield start()
        await asyncio.sleep(1)

    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            call = pb_grpc.EnhancementServiceStub(channel).Enhance(
                idle_messages(), metadata=(("authorization", f"Bearer {token}"),)
            )
            with pytest.raises(grpc.aio.AioRpcError) as failure:
                async for _ in call:
                    continue
            assert failure.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED
    finally:
        await server.stop(0)
        await scheduler.close()


@pytest.mark.integration
async def test_active_stream_id_is_unique_and_reusable_after_close() -> None:
    import asyncio

    server, scheduler, port, token = await start_server()
    release = asyncio.Event()

    async def held_stream() -> AsyncIterator[pb.ClientMessage]:
        yield start("held")
        await release.wait()
        yield pb.ClientMessage(end=pb.EndStream(flush=True))

    metadata = (("authorization", f"Bearer {token}"),)
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = pb_grpc.EnhancementServiceStub(channel)
            first = stub.Enhance(held_stream(), metadata=metadata)
            assert (await first.read()).WhichOneof("body") == "accepted"

            duplicate = stub.Enhance(
                messages([start("held"), pb.ClientMessage(end=pb.EndStream(flush=True))]),
                metadata=metadata,
            )
            with pytest.raises(grpc.aio.AioRpcError) as failure:
                await duplicate.read()
            assert failure.value.code() == grpc.StatusCode.ALREADY_EXISTS

            release.set()
            assert (await first.read()).WhichOneof("body") == "ended"

            reused = stub.Enhance(
                messages([start("held"), pb.ClientMessage(end=pb.EndStream(flush=True))]),
                metadata=metadata,
            )
            assert [response.WhichOneof("body") async for response in reused] == [
                "accepted",
                "ended",
            ]
    finally:
        release.set()
        await server.stop(0)
        await scheduler.close()


@pytest.mark.integration
async def test_active_stream_limit_and_cancellation_cleanup() -> None:
    import asyncio

    server, scheduler, port, token = await start_server(max_active_streams=1)
    release = asyncio.Event()

    async def held_stream() -> AsyncIterator[pb.ClientMessage]:
        yield start("held-limit")
        await release.wait()

    metadata = (("authorization", f"Bearer {token}"),)
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = pb_grpc.EnhancementServiceStub(channel)
            held = stub.Enhance(held_stream(), metadata=metadata)
            assert (await held.read()).WhichOneof("body") == "accepted"

            rejected = stub.Enhance(
                messages([start("second"), pb.ClientMessage(end=pb.EndStream(flush=True))]),
                metadata=metadata,
            )
            with pytest.raises(grpc.aio.AioRpcError) as failure:
                await rejected.read()
            assert failure.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED

            held.cancel()
            assert await held.code() == grpc.StatusCode.CANCELLED
            release.set()
            deadline = asyncio.get_running_loop().time() + 1
            while True:
                reused = stub.Enhance(
                    messages([start("held-limit"), pb.ClientMessage(end=pb.EndStream(flush=True))]),
                    metadata=metadata,
                )
                try:
                    assert [response.WhichOneof("body") async for response in reused] == [
                        "accepted",
                        "ended",
                    ]
                    break
                except grpc.aio.AioRpcError as exc:
                    if (
                        exc.code()
                        not in {
                            grpc.StatusCode.ALREADY_EXISTS,
                            grpc.StatusCode.RESOURCE_EXHAUSTED,
                        }
                        or asyncio.get_running_loop().time() >= deadline
                    ):
                        raise
                    await asyncio.sleep(0.01)
    finally:
        release.set()
        await server.stop(0)
        await scheduler.close()


@pytest.mark.integration
async def test_oversized_chunk_is_resource_exhausted() -> None:
    server, scheduler, port, token = await start_server()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            call = pb_grpc.EnhancementServiceStub(channel).Enhance(
                messages(
                    [
                        start(),
                        pb.ClientMessage(
                            audio=pb.AudioChunk(
                                sequence=0,
                                input_start_sample=100,
                                pcm_s16le=bytes(16_001 * 2),
                            )
                        ),
                    ]
                ),
                metadata=(("authorization", f"Bearer {token}"),),
            )
            with pytest.raises(grpc.aio.AioRpcError) as failure:
                async for _ in call:
                    continue
            assert failure.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED
    finally:
        await server.stop(0)
        await scheduler.close()


@pytest.mark.integration
async def test_no_flush_processes_queued_hops_but_drops_only_delayed_tail() -> None:
    server, scheduler, port, token = await start_server()
    pcm = b"\x01\x00" * 512
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            responses = [
                response
                async for response in pb_grpc.EnhancementServiceStub(channel).Enhance(
                    messages(
                        [
                            start(offset=0),
                            pb.ClientMessage(
                                audio=pb.AudioChunk(sequence=0, input_start_sample=0, pcm_s16le=pcm)
                            ),
                            pb.ClientMessage(end=pb.EndStream(flush=False)),
                        ]
                    ),
                    metadata=(("authorization", f"Bearer {token}"),),
                )
            ]
        audio = [response.audio for response in responses if response.WhichOneof("body") == "audio"]
        assert b"".join(value.pcm_s16le for value in audio) == pcm[: 256 * 2]
        assert responses[-1].ended.input_samples == 512
        assert responses[-1].ended.output_samples == 256
        assert not responses[-1].ended.flushed
    finally:
        await server.stop(0)
        await scheduler.close()
