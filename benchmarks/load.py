#!/usr/bin/env python3
"""Real-time multi-stream load generator with correctness and performance gates."""

from __future__ import annotations

import array
import asyncio
import json
import math
import os
import subprocess
import time
import urllib.request
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import grpc
import psutil
from fastenhancer.v1 import enhancement_pb2 as pb
from fastenhancer.v1 import enhancement_pb2_grpc as pb_grpc
from livekit import rtc
from livekit.plugins.fastenhancer import RemoteFastEnhancer

HOP_SAMPLES = 256
HOP_SECONDS = HOP_SAMPLES / 16_000
A6000_UUID = "GPU-bac67bca-195d-3490-88f0-b8a3453c5929"


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    left = int(position)
    right = min(left + 1, len(ordered) - 1)
    fraction = position - left
    return ordered[left] * (1 - fraction) + ordered[right] * fraction


def metric_snapshot(url: str) -> dict[str, float]:
    body = urllib.request.urlopen(url, timeout=5).read().decode()  # noqa: S310
    result: dict[str, float] = {}
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        name, value = line.rsplit(" ", 1)
        result[name] = float(value)
    return result


def histogram_quantile(
    before: dict[str, float], after: dict[str, float], prefix: str, quantile: float
) -> float | None:
    buckets: list[tuple[float, float]] = []
    marker = prefix + '_bucket{le="'
    for name, value in after.items():
        if not name.startswith(marker):
            continue
        boundary_text = name[len(marker) :].split('"', 1)[0]
        boundary = math.inf if boundary_text == "+Inf" else float(boundary_text)
        buckets.append((boundary, value - before.get(name, 0.0)))
    buckets.sort()
    if not buckets or buckets[-1][1] <= 0:
        return None
    target = buckets[-1][1] * quantile
    for boundary, cumulative in buckets:
        if cumulative >= target:
            return None if math.isinf(boundary) else boundary
    return None


def histogram_summary(
    before: dict[str, float], after: dict[str, float], prefix: str, scale: float = 1.0
) -> dict[str, float | None]:
    return {
        "p50": (
            value * scale
            if (value := histogram_quantile(before, after, prefix, 0.50)) is not None
            else None
        ),
        "p95": (
            value * scale
            if (value := histogram_quantile(before, after, prefix, 0.95)) is not None
            else None
        ),
        "p99": (
            value * scale
            if (value := histogram_quantile(before, after, prefix, 0.99)) is not None
            else None
        ),
    }


def batch_distribution(before: dict[str, float], after: dict[str, float]) -> dict[str, Any]:
    prefix = 'fastenhancer_batch_size_bucket{le="'
    cumulative: list[tuple[str, float]] = []
    for name, value in after.items():
        if name.startswith(prefix):
            boundary = name[len(prefix) :].split('"', 1)[0]
            cumulative.append((boundary, value - before.get(name, 0.0)))
    cumulative.sort(key=lambda item: math.inf if item[0] == "+Inf" else float(item[0]))
    exact: dict[str, float] = {}
    previous = 0.0
    for boundary, count in cumulative:
        exact[boundary] = count - previous
        previous = count
    count_delta = after.get("fastenhancer_batch_size_count", 0.0) - before.get(
        "fastenhancer_batch_size_count", 0.0
    )
    sum_delta = after.get("fastenhancer_batch_size_sum", 0.0) - before.get(
        "fastenhancer_batch_size_sum", 0.0
    )
    return {
        "bucket_counts": exact,
        "count": count_delta,
        "mean": sum_delta / count_delta if count_delta else None,
    }


def sine_hop(stream_index: int, hop_index: int) -> bytes:
    frequency = 220 + stream_index * 7
    base = hop_index * HOP_SAMPLES
    return array.array(
        "h",
        [
            round(3000 * math.sin(2 * math.pi * frequency * (base + index) / 16_000))
            for index in range(HOP_SAMPLES)
        ],
    ).tobytes()


async def request_stream(
    stream_index: int,
    hop_count: int,
    barrier: asyncio.Event,
    sent_at: dict[int, float],
    audio_hops: list[bytes],
) -> AsyncIterator[pb.ClientMessage]:
    yield pb.ClientMessage(
        start=pb.StartStream(
            protocol_version="1",
            stream_id=f"benchmark-{stream_index}-{time.time_ns()}",
            input_start_sample=0,
            sample_rate_hz=16_000,
            channels=1,
            sample_format=pb.SAMPLE_FORMAT_PCM_S16LE,
            metadata={"client": "benchmark"},
        )
    )
    await barrier.wait()
    started = asyncio.get_running_loop().time()
    for index in range(hop_count):
        deadline = started + index * HOP_SECONDS
        await asyncio.sleep(max(0, deadline - asyncio.get_running_loop().time()))
        offset = index * HOP_SAMPLES
        sent_at[offset] = time.perf_counter()
        yield pb.ClientMessage(
            audio=pb.AudioChunk(
                sequence=index,
                input_start_sample=offset,
                pcm_s16le=audio_hops[index],
            )
        )
    yield pb.ClientMessage(end=pb.EndStream(flush=True))


async def one_stream(
    endpoint: str,
    token: str,
    stream_index: int,
    hop_count: int,
    warmup_hops: int,
    barrier: asyncio.Event,
) -> dict[str, Any]:
    sent_at: dict[int, float] = {}
    audio_hops = [sine_hop(stream_index, index) for index in range(hop_count)]
    rtt_ms: list[float] = []
    expected_offset = 0
    output_samples = 0
    ended = False
    error: str | None = None
    try:
        async with grpc.aio.insecure_channel(endpoint) as channel:
            stub = pb_grpc.EnhancementServiceStub(channel)
            async for response in stub.Enhance(
                request_stream(stream_index, hop_count, barrier, sent_at, audio_hops),
                metadata=(("authorization", f"Bearer {token}"),),
            ):
                body = response.WhichOneof("body")
                if body == "audio":
                    audio = response.audio
                    if audio.output_start_sample != expected_offset:
                        raise RuntimeError(
                            f"output offset {audio.output_start_sample}, expected {expected_offset}"
                        )
                    if len(audio.pcm_s16le) != audio.valid_samples * 2:
                        raise RuntimeError("output byte count mismatch")
                    if audio.output_start_sample >= warmup_hops * HOP_SAMPLES:
                        rtt_ms.append(
                            (time.perf_counter() - sent_at[audio.output_start_sample]) * 1000
                        )
                    expected_offset += audio.valid_samples
                    output_samples += audio.valid_samples
                elif body == "ended":
                    if response.ended.input_samples != hop_count * HOP_SAMPLES:
                        raise RuntimeError("server input accounting mismatch")
                    if response.ended.output_samples != hop_count * HOP_SAMPLES:
                        raise RuntimeError("server output accounting mismatch")
                    ended = True
        if not ended or output_samples != hop_count * HOP_SAMPLES:
            raise RuntimeError("stream ended without an exact flushed output")
    except Exception as exc:
        error = str(exc)
    return {
        "stream": stream_index,
        "input_samples": hop_count * HOP_SAMPLES,
        "output_samples": output_samples,
        "rtt_ms": rtt_ms,
        "error": error,
    }


def plugin_probe(
    endpoint: str, token: str, duration_s: float, response_wait_ms: float
) -> dict[str, Any]:
    processor = RemoteFastEnhancer(
        endpoint, api_key=token, tls=False, response_wait_ms=response_wait_ms
    )
    hop_count = max(4, round(duration_s / HOP_SECONDS))
    audio_hops = [sine_hop(997, index) for index in range(hop_count)]
    started = time.perf_counter()
    output_samples = 0
    try:
        for index in range(hop_count):
            deadline = started + index * HOP_SECONDS
            time.sleep(max(0, deadline - time.perf_counter()))
            value = audio_hops[index]
            frame = rtc.AudioFrame(value, 16_000, 1, HOP_SAMPLES)
            output_samples += processor._process(frame).samples_per_channel
        metrics = processor.metrics
    finally:
        processor._close()
    eligible = max(0, hop_count * HOP_SAMPLES - 256)
    if output_samples != eligible:
        raise RuntimeError("plugin probe output does not match the delayed timeline")
    fallback_ratio = metrics["raw_fallback_samples"] / eligible if eligible else 0.0
    return {
        "input_samples": hop_count * HOP_SAMPLES,
        "output_samples": output_samples,
        "eligible_samples": eligible,
        "fallback_ratio": fallback_ratio,
        "response_wait_ms": response_wait_ms,
        "metrics": metrics,
    }


def gpu_telemetry() -> dict[str, Any]:
    command = [
        "nvidia-smi",
        "-i",
        A6000_UUID,
        "--query-gpu=uuid,name,utilization.gpu,memory.used,memory.total",
        "--format=csv,noheader,nounits",
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)  # noqa: S603
    if result.returncode:
        return {"available": False, "error": result.stderr.strip()}
    uuid, name, utilization, memory_used, memory_total = [
        value.strip() for value in result.stdout.strip().split(",")
    ]
    return {
        "available": True,
        "uuid": uuid,
        "name": name,
        "utilization_percent": float(utilization),
        "memory_used_mib": float(memory_used),
        "memory_total_mib": float(memory_total),
    }


async def gpu_telemetry_during_load(stop: asyncio.Event) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    while True:
        samples.append(await asyncio.to_thread(gpu_telemetry))
        if stop.is_set():
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.2)
        except TimeoutError:
            continue
    valid = [sample for sample in samples if sample.get("available")]
    identities = {(sample.get("uuid"), sample.get("name")) for sample in valid}
    expected_identity = (A6000_UUID, "NVIDIA RTX A6000")
    return {
        "available": len(valid) == len(samples) and identities == {expected_identity},
        "sample_count": len(samples),
        "uuid": valid[0]["uuid"] if valid else None,
        "name": valid[0]["name"] if valid else None,
        "utilization_percent": {
            "mean": (
                sum(sample["utilization_percent"] for sample in valid) / len(valid)
                if valid
                else None
            ),
            "max": max((sample["utilization_percent"] for sample in valid), default=None),
        },
        "memory_used_mib": {
            "min": min((sample["memory_used_mib"] for sample in valid), default=None),
            "max": max((sample["memory_used_mib"] for sample in valid), default=None),
        },
        "errors": [sample.get("error") for sample in samples if not sample.get("available")],
    }


def host_telemetry(endpoint: str) -> dict[str, Any]:
    port = int(endpoint.rsplit(":", 1)[1])
    owner: dict[str, Any] | None = None
    for connection in psutil.net_connections(kind="tcp"):
        if connection.status == psutil.CONN_LISTEN and connection.laddr.port == port:
            if connection.pid is not None:
                process = psutil.Process(connection.pid)
                owner = {
                    "pid": process.pid,
                    "name": process.name(),
                    "rss_bytes": process.memory_info().rss,
                    "cpu_percent": process.cpu_percent(interval=0.1),
                }
            break
    return {
        "host_cpu_percent": psutil.cpu_percent(interval=0.1),
        "host_memory": psutil.virtual_memory()._asdict(),
        "listening_process": owner,
    }


async def benchmark_level(
    streams: int,
    duration_s: float,
    warmup_s: float,
    endpoint: str,
    token: str,
    metrics_url: str,
) -> dict[str, Any]:
    hop_count = max(2, round((duration_s + warmup_s) / HOP_SECONDS))
    warmup_hops = round(warmup_s / HOP_SECONDS)
    before = await asyncio.to_thread(metric_snapshot, metrics_url)
    barrier = asyncio.Event()
    tasks = [
        asyncio.create_task(one_stream(endpoint, token, index, hop_count, warmup_hops, barrier))
        for index in range(streams)
    ]
    telemetry_stop = asyncio.Event()
    telemetry_task = asyncio.create_task(gpu_telemetry_during_load(telemetry_stop))
    wall_started = time.perf_counter()
    barrier.set()
    try:
        results = await asyncio.gather(*tasks)
    finally:
        telemetry_stop.set()
    gpu_during_load = await telemetry_task
    wall_seconds = time.perf_counter() - wall_started
    after = await asyncio.to_thread(metric_snapshot, metrics_url)
    rtt = [value for result in results for value in result["rtt_ms"]]
    errors = [result["error"] for result in results if result["error"]]
    total_audio_seconds = streams * hop_count * HOP_SECONDS
    return {
        "streams": streams,
        "wall_seconds": wall_seconds,
        "audio_seconds": total_audio_seconds,
        "real_time_factor": wall_seconds / total_audio_seconds,
        "input_samples": [result["input_samples"] for result in results],
        "output_samples": [result["output_samples"] for result in results],
        "rtt_ms": {
            "count": len(rtt),
            "p50": percentile(rtt, 0.50),
            "p95": percentile(rtt, 0.95),
            "p99": percentile(rtt, 0.99),
        },
        "server_ms": {
            "batch_wait": histogram_summary(before, after, "fastenhancer_batch_wait_seconds", 1000),
            "inference": histogram_summary(before, after, "fastenhancer_inference_seconds", 1000),
            "hop_end_to_end": histogram_summary(
                before, after, "fastenhancer_hop_end_to_end_seconds", 1000
            ),
        },
        "batch_distribution": batch_distribution(before, after),
        "gpu_during_load": gpu_during_load,
        "errors": errors,
        "error_ratio": len(errors) / streams,
    }


async def main() -> int:
    endpoint = os.environ.get("BENCH_ENDPOINT", "127.0.0.1:50051")
    metrics_url = os.environ.get("BENCH_METRICS_URL", "http://127.0.0.1:8080/metrics")
    token = os.environ.get("FASTENHANCER_API_TOKEN")
    if not token:
        raise ValueError("FASTENHANCER_API_TOKEN is required")
    duration_s = float(os.environ.get("BENCH_DURATION_S", "10"))
    warmup_s = float(os.environ.get("BENCH_WARMUP_S", "2"))
    target = os.environ.get("BENCH_TARGET_STREAMS")
    levels = (
        [int(target)]
        if target
        else [int(x) for x in os.environ.get("BENCH_STREAM_LEVELS", "1,8,16,32,64").split(",")]
    )
    capabilities: dict[str, Any]
    async with grpc.aio.insecure_channel(endpoint) as channel:
        response = await pb_grpc.EnhancementServiceStub(channel).GetCapabilities(
            pb.GetCapabilitiesRequest(),
            metadata=(("authorization", f"Bearer {token}"),),
        )
        capabilities = {
            "model": response.capabilities.model_name,
            "revision": response.capabilities.model_revision,
            "checkpoint_sha256": response.capabilities.model_sha256,
            "cuda_device": response.capabilities.cuda_device,
        }
    level_results = [
        await benchmark_level(level, duration_s, warmup_s, endpoint, token, metrics_url)
        for level in levels
    ]
    plugin_wait_ms = float(os.environ.get("BENCH_PLUGIN_RESPONSE_WAIT_MS", "100"))
    probe = await asyncio.to_thread(
        plugin_probe, endpoint, token, min(duration_s, 3.0), plugin_wait_ms
    )
    gpu = gpu_telemetry()
    thresholds = {
        "server_inference_p95_ms": float(os.environ.get("BENCH_MAX_SERVER_P95_MS", "25")),
        "rtt_p95_ms": float(os.environ.get("BENCH_MAX_RTT_P95_MS", "100")),
        "fallback_ratio": float(os.environ.get("BENCH_MAX_FALLBACK_RATIO", "0")),
        "error_ratio": float(os.environ.get("BENCH_MAX_ERROR_RATIO", "0")),
    }
    failures: list[str] = []
    for result in level_results:
        inference_p95 = result["server_ms"]["inference"]["p95"]
        if inference_p95 is None or inference_p95 > thresholds["server_inference_p95_ms"]:
            failures.append(f"{result['streams']} streams exceeded server p95 gate")
        if not result["rtt_ms"]["count"] or result["rtt_ms"]["p95"] > thresholds["rtt_p95_ms"]:
            failures.append(f"{result['streams']} streams exceeded RTT p95 gate")
        if result["error_ratio"] > thresholds["error_ratio"]:
            failures.append(f"{result['streams']} streams exceeded error gate")
        if not result["gpu_during_load"]["available"]:
            failures.append(f"{result['streams']} streams failed A6000 load telemetry")
    if probe["fallback_ratio"] > thresholds["fallback_ratio"]:
        failures.append("plugin raw fallback gate failed")
    if (
        not gpu.get("available")
        or gpu.get("uuid") != A6000_UUID
        or gpu.get("name") != "NVIDIA RTX A6000"
    ):
        failures.append("A6000 telemetry gate failed")
    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "endpoint": endpoint,
        "duration_s": duration_s,
        "warmup_s": warmup_s,
        "capabilities": capabilities,
        "levels": level_results,
        "plugin_probe": probe,
        "gpu": gpu,
        "host": host_telemetry(endpoint),
        "host_load_average": os.getloadavg(),
        "image_digest": os.environ.get("BENCH_IMAGE_DIGEST"),
        "gates": {
            "passed": not failures,
            "thresholds": thresholds,
            "failures": failures,
        },
    }
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(os.environ.get("BENCH_OUTPUT_DIR", f"artifacts/validation/{timestamp}"))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "benchmark.json").write_text(json.dumps(report, indent=2) + "\n")
    lines = [
        "# FastEnhancer benchmark",
        "",
        f"Model: `{capabilities['model']}` on `{capabilities['cuda_device']}`",
        "",
        "| Streams | RTT p50 ms | RTT p95 ms | RTT p99 ms | Inference p95 ms | Errors |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for result in level_results:
        lines.append(
            f"| {result['streams']} | {result['rtt_ms']['p50']:.3f} | "
            f"{result['rtt_ms']['p95']:.3f} | {result['rtt_ms']['p99']:.3f} | "
            f"{result['server_ms']['inference']['p95']} | {len(result['errors'])} |"
        )
    lines.extend(["", f"Plugin raw fallback ratio: `{probe['fallback_ratio']:.6f}`", ""])
    (output_dir / "benchmark.md").write_text("\n".join(lines))
    print(f"wrote benchmark artifacts to {output_dir}")
    if failures:
        for failure in failures:
            print(f"FAILED: {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
