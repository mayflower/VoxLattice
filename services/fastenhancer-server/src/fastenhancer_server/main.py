"""Server process entry point."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from pathlib import Path
from typing import Any

import grpc
from fastenhancer.v1 import enhancement_pb2_grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from .health import HealthHTTPServer
from .metrics import ServerMetrics
from .model import FastEnhancerBStreamingModel
from .scheduler import BatchScheduler
from .self_test import validate_state_isolation
from .service import EnhancementService
from .settings import ServerSettings

SERVICE_NAME = "fastenhancer.v1.EnhancementService"


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            value["exception"] = self.formatException(record.exc_info)
        return json.dumps(value, separators=(",", ":"))


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def _read(path: Path) -> bytes:
    return path.read_bytes()


def _server_credentials(settings: ServerSettings) -> grpc.ServerCredentials:
    if settings.tls_certificate is None or settings.tls_private_key is None:
        raise RuntimeError("TLS files are not configured")
    root_certificates = _read(settings.tls_client_ca) if settings.tls_client_ca else None
    return grpc.ssl_server_credentials(
        [(_read(settings.tls_private_key), _read(settings.tls_certificate))],
        root_certificates=root_certificates,
        require_client_auth=root_certificates is not None,
    )


async def run() -> None:
    logger = logging.getLogger("fastenhancer_server")
    settings = ServerSettings.from_environment()
    metrics = ServerMetrics()
    ready = asyncio.Event()
    health_http = HealthHTTPServer(metrics, ready)
    model = FastEnhancerBStreamingModel(
        checkpoint_path=settings.checkpoint_path,
        manifest_path=settings.manifest_path,
        config_path=settings.model_config_path,
        cuda_device=settings.cuda_device,
    )
    model.warm_up((1, 2, min(8, settings.max_batch_size), settings.max_batch_size))
    validate_state_isolation(model)
    metrics.info.info(
        {
            "model": model.model_name,
            "revision": model.model_revision,
            "checkpoint_sha256": model.checkpoint_sha256,
            "cuda_device": model.cuda_device_name,
        }
    )
    scheduler = BatchScheduler(
        model,
        metrics,
        max_active_streams=settings.max_active_streams,
        max_batch_size=settings.max_batch_size,
        max_batch_wait_ms=settings.max_batch_wait_ms,
    )
    scheduler.start()
    grpc_server = grpc.aio.server(
        options=(
            ("grpc.max_receive_message_length", settings.max_audio_chunk_samples * 2 + 4096),
            ("grpc.max_send_message_length", settings.max_audio_chunk_samples * 2 + 4096),
            ("grpc.keepalive_time_ms", 20_000),
            ("grpc.keepalive_timeout_ms", 10_000),
            ("grpc.http2.max_pings_without_data", 2),
        )
    )
    service = EnhancementService(
        model=model, scheduler=scheduler, settings=settings, metrics=metrics
    )
    enhancement_pb2_grpc.add_EnhancementServiceServicer_to_server(  # type: ignore[no-untyped-call]
        service, grpc_server
    )
    grpc_health = health.aio.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(grpc_health, grpc_server)
    await grpc_health.set("", health_pb2.HealthCheckResponse.NOT_SERVING)
    await grpc_health.set(SERVICE_NAME, health_pb2.HealthCheckResponse.NOT_SERVING)
    address = f"{settings.grpc_host}:{settings.grpc_port}"
    if settings.allow_insecure:
        bound_port = grpc_server.add_insecure_port(address)
    else:
        bound_port = grpc_server.add_secure_port(address, _server_credentials(settings))
    if bound_port == 0:
        raise RuntimeError(f"could not bind gRPC address {address}")
    await health_http.start(settings.http_host, settings.http_port)
    await grpc_server.start()
    ready.set()
    await grpc_health.set("", health_pb2.HealthCheckResponse.SERVING)
    await grpc_health.set(SERVICE_NAME, health_pb2.HealthCheckResponse.SERVING)
    logger.info(
        "server ready",
        extra={"cuda_device": model.cuda_device_name, "model_revision": model.model_revision},
    )
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stopping.set)
    await stopping.wait()
    ready.clear()
    await grpc_health.set("", health_pb2.HealthCheckResponse.NOT_SERVING)
    await grpc_health.set(SERVICE_NAME, health_pb2.HealthCheckResponse.NOT_SERVING)
    await grpc_server.stop(settings.graceful_shutdown_s)
    await scheduler.close()
    await health_http.close()
    logger.info("server stopped")


def main() -> None:
    _configure_logging()
    asyncio.run(run())


if __name__ == "__main__":
    main()
