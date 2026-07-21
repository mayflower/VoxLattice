#!/usr/bin/env python3
"""Stream WAV, stdin PCM, or deterministic synthetic audio through FastEnhancer."""

from __future__ import annotations

import argparse
import array
import asyncio
import math
import os
import sys
import wave
from collections.abc import AsyncIterator
from pathlib import Path

import grpc
from fastenhancer.v1 import enhancement_pb2 as pb
from fastenhancer.v1 import enhancement_pb2_grpc as pb_grpc


def _api_token(args: argparse.Namespace) -> str:
    token = args.api_key or os.environ.get("FASTENHANCER_API_TOKEN")
    token_file = os.environ.get("FASTENHANCER_API_TOKEN_FILE")
    if token and token_file:
        raise ValueError("configure only one API token source")
    if token_file:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError(
            "--api-key, FASTENHANCER_API_TOKEN, or FASTENHANCER_API_TOKEN_FILE is required"
        )
    return token


def _resample_mono_pcm16(data: bytes, source_rate: int, channels: int) -> bytes:
    samples = array.array("h")
    samples.frombytes(data)
    if channels > 1:
        samples = array.array(
            "h",
            [
                round(sum(samples[index : index + channels]) / channels)
                for index in range(0, len(samples), channels)
            ],
        )
    if source_rate == 16_000:
        return samples.tobytes()
    output_count = round(len(samples) * 16_000 / source_rate)
    output = array.array("h")
    for index in range(output_count):
        position = index * source_rate / 16_000
        left = min(int(position), len(samples) - 1)
        right = min(left + 1, len(samples) - 1)
        fraction = position - left
        output.append(round(samples[left] * (1 - fraction) + samples[right] * fraction))
    return output.tobytes()


def _load_input(args: argparse.Namespace) -> bytes:
    if args.input == "-":
        return sys.stdin.buffer.read()
    if args.input:
        with wave.open(args.input, "rb") as source:
            if source.getsampwidth() != 2:
                raise ValueError("input WAV must contain PCM16 samples")
            return _resample_mono_pcm16(
                source.readframes(source.getnframes()), source.getframerate(), source.getnchannels()
            )
    count = round(args.duration_s * 16_000)
    return array.array(
        "h", [round(4000 * math.sin(2 * math.pi * 440 * index / 16_000)) for index in range(count)]
    ).tobytes()


async def _requests(
    pcm: bytes, chunk_samples: int, realtime: bool
) -> AsyncIterator[pb.ClientMessage]:
    yield pb.ClientMessage(
        start=pb.StartStream(
            protocol_version="1",
            stream_id=f"smoke-{os.getpid()}",
            input_start_sample=0,
            sample_rate_hz=16_000,
            channels=1,
            sample_format=pb.SAMPLE_FORMAT_PCM_S16LE,
            metadata={"client": "grpc-example"},
        )
    )
    for sequence, byte_start in enumerate(range(0, len(pcm), chunk_samples * 2)):
        value = pcm[byte_start : byte_start + chunk_samples * 2]
        yield pb.ClientMessage(
            audio=pb.AudioChunk(
                sequence=sequence, input_start_sample=byte_start // 2, pcm_s16le=value
            )
        )
        if realtime:
            await asyncio.sleep(len(value) / 2 / 16_000)
    yield pb.ClientMessage(end=pb.EndStream(flush=True))


async def run(args: argparse.Namespace) -> None:
    token = _api_token(args)
    pcm = _load_input(args)
    if bool(args.client_certificate) != bool(args.client_private_key):
        raise ValueError("--client-certificate and --client-private-key are required together")
    if args.client_certificate and not args.tls:
        raise ValueError("client certificates require --tls")
    credentials = grpc.ssl_channel_credentials(
        root_certificates=(
            Path(args.root_certificate).read_bytes() if args.root_certificate else None
        ),
        private_key=(
            Path(args.client_private_key).read_bytes() if args.client_private_key else None
        ),
        certificate_chain=(
            Path(args.client_certificate).read_bytes() if args.client_certificate else None
        ),
    )
    channel_factory = grpc.aio.secure_channel if args.tls else grpc.aio.insecure_channel
    channel_args = (args.endpoint, credentials) if args.tls else (args.endpoint,)
    metadata = (("authorization", f"Bearer {token}"),)
    async with channel_factory(*channel_args) as channel:
        stub = pb_grpc.EnhancementServiceStub(channel)
        capabilities = await stub.GetCapabilities(pb.GetCapabilitiesRequest(), metadata=metadata)
        print(
            f"model={capabilities.capabilities.model_name} "
            f"revision={capabilities.capabilities.model_revision} "
            f"device={capabilities.capabilities.cuda_device}"
        )
        output = bytearray()
        expected_offset = 0
        ended: pb.StreamEnded | None = None
        async for response in stub.Enhance(
            _requests(pcm, args.chunk_samples, args.realtime), metadata=metadata
        ):
            body = response.WhichOneof("body")
            if body == "audio":
                if response.audio.output_start_sample != expected_offset:
                    raise RuntimeError("server output offset mismatch")
                if len(response.audio.pcm_s16le) != response.audio.valid_samples * 2:
                    raise RuntimeError("server output length mismatch")
                output.extend(response.audio.pcm_s16le)
                expected_offset += response.audio.valid_samples
            elif body == "ended":
                ended = response.ended
        if (
            ended is None
            or ended.input_samples != len(pcm) // 2
            or ended.output_samples != len(pcm) // 2
        ):
            raise RuntimeError("clean flush did not preserve exact sample count")
        if len(output) != len(pcm):
            raise RuntimeError("output PCM byte length differs from input")
    if args.output:
        with wave.open(args.output, "wb") as destination:
            destination.setnchannels(1)
            destination.setsampwidth(2)
            destination.setframerate(16_000)
            destination.writeframes(output)
    print(f"verified {len(pcm) // 2} input/output samples")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="127.0.0.1:50051")
    parser.add_argument("--api-key")
    parser.add_argument("--tls", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--root-certificate")
    parser.add_argument("--client-certificate")
    parser.add_argument("--client-private-key")
    parser.add_argument("--input", help="WAV path or '-' for raw 16-kHz mono PCM16 stdin")
    parser.add_argument("--output", help="enhanced WAV destination")
    parser.add_argument("--duration-s", type=float, default=1.0)
    parser.add_argument("--chunk-samples", type=int, default=256)
    parser.add_argument("--realtime", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
