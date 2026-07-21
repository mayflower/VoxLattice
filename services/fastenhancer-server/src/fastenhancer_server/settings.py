"""Validated server configuration sourced only from explicit environment values."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _positive_int(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_float(name: str, default: float) -> float:
    value = float(os.environ.get(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "true" if default else "false").lower()
    if raw not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return raw == "true"


@dataclass(frozen=True, slots=True)
class ServerSettings:
    grpc_host: str
    grpc_port: int
    http_host: str
    http_port: int
    checkpoint_path: Path
    manifest_path: Path
    model_config_path: Path
    cuda_device: str
    required_device_name: str
    api_token: str = field(repr=False)
    allow_insecure: bool
    tls_certificate: Path | None
    tls_private_key: Path | None
    tls_client_ca: Path | None
    max_active_streams: int
    max_batch_size: int
    max_batch_wait_ms: float
    max_pending_audio_ms_per_stream: int
    max_output_audio_ms_per_stream: int
    stream_idle_timeout_s: float
    max_audio_chunk_samples: int
    graceful_shutdown_s: float

    @classmethod
    def from_environment(cls) -> ServerSettings:
        token = os.environ.get("FASTENHANCER_API_TOKEN", "")
        token_file = os.environ.get("FASTENHANCER_API_TOKEN_FILE")
        if token and token_file:
            raise ValueError("configure only one of FASTENHANCER_API_TOKEN or *_FILE")
        if token_file:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        if len(token) < 16:
            raise ValueError("a non-default API token of at least 16 characters is required")
        allow_insecure = _boolean("ALLOW_INSECURE_GRPC")
        certificate = os.environ.get("TLS_CERTIFICATE")
        private_key = os.environ.get("TLS_PRIVATE_KEY")
        client_ca = os.environ.get("TLS_CLIENT_CA")
        if not allow_insecure and (not certificate or not private_key):
            raise ValueError("TLS_CERTIFICATE and TLS_PRIVATE_KEY are required")
        if bool(certificate) != bool(private_key):
            raise ValueError("TLS certificate and private key must be configured together")
        return cls(
            grpc_host=os.environ.get("GRPC_HOST", "0.0.0.0"),  # noqa: S104
            grpc_port=_positive_int("GRPC_PORT", 50051),
            http_host=os.environ.get("HTTP_HOST", "0.0.0.0"),  # noqa: S104
            http_port=_positive_int("HTTP_PORT", 8080),
            checkpoint_path=Path(os.environ.get("MODEL_CHECKPOINT", "/opt/model/00500.pth")),
            manifest_path=Path(os.environ.get("MODEL_MANIFEST", "/opt/model/manifest.lock.json")),
            model_config_path=Path(os.environ.get("MODEL_CONFIG", "/opt/model/config.yaml")),
            cuda_device=os.environ.get("CUDA_DEVICE", "cuda:0"),
            required_device_name=os.environ.get("REQUIRED_CUDA_DEVICE_NAME", "NVIDIA RTX A6000"),
            api_token=token,
            allow_insecure=allow_insecure,
            tls_certificate=Path(certificate) if certificate else None,
            tls_private_key=Path(private_key) if private_key else None,
            tls_client_ca=Path(client_ca) if client_ca else None,
            max_active_streams=_positive_int("MAX_ACTIVE_STREAMS", 128),
            max_batch_size=_positive_int("MAX_BATCH_SIZE", 32),
            max_batch_wait_ms=_positive_float("MAX_BATCH_WAIT_MS", 1.0),
            max_pending_audio_ms_per_stream=_positive_int("MAX_PENDING_AUDIO_MS_PER_STREAM", 500),
            max_output_audio_ms_per_stream=_positive_int("MAX_OUTPUT_AUDIO_MS_PER_STREAM", 500),
            stream_idle_timeout_s=_positive_float("STREAM_IDLE_TIMEOUT_S", 30.0),
            max_audio_chunk_samples=_positive_int("MAX_AUDIO_CHUNK_SAMPLES", 16_000),
            graceful_shutdown_s=_positive_float("GRACEFUL_SHUTDOWN_S", 10.0),
        )
