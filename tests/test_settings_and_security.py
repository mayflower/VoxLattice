from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import grpc
import pytest
import torch
import trustme
from fastenhancer.v1 import enhancement_pb2 as pb
from fastenhancer.v1 import enhancement_pb2_grpc as pb_grpc
from fastenhancer_server.main import _server_credentials
from fastenhancer_server.metrics import ServerMetrics
from fastenhancer_server.model import FastEnhancerBStreamingModel
from fastenhancer_server.scheduler import BatchScheduler
from fastenhancer_server.service import EnhancementService
from fastenhancer_server.settings import ServerSettings
from livekit import rtc
from livekit.plugins.fastenhancer import RemoteFastEnhancer, RemoteFastEnhancerConfig

from .fakes import IdentityDelayModel
from .test_grpc_service import settings


def test_settings_require_real_secret_and_explicit_insecure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "FASTENHANCER_API_TOKEN",
        "FASTENHANCER_API_TOKEN_FILE",
        "ALLOW_INSECURE_GRPC",
        "TLS_CERTIFICATE",
        "TLS_PRIVATE_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="API token"):
        ServerSettings.from_environment()
    monkeypatch.setenv("FASTENHANCER_API_TOKEN", "a-secure-test-token")
    with pytest.raises(ValueError, match="TLS_CERTIFICATE"):
        ServerSettings.from_environment()
    monkeypatch.setenv("ALLOW_INSECURE_GRPC", "true")
    assert ServerSettings.from_environment().allow_insecure


def test_secret_file_is_trimmed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = tmp_path / "token"
    secret.write_text("file-backed-test-token\n")
    monkeypatch.delenv("FASTENHANCER_API_TOKEN", raising=False)
    monkeypatch.setenv("FASTENHANCER_API_TOKEN_FILE", str(secret))
    monkeypatch.setenv("ALLOW_INSECURE_GRPC", "true")
    assert (
        ServerSettings.from_environment().api_token == "file-backed-test-token"  # noqa: S105
    )


def test_secret_values_are_redacted_from_configuration_repr() -> None:
    token = "repr-must-not-contain-this-token"  # noqa: S105
    private_key = b"repr-must-not-contain-this-private-key"
    assert token not in repr(settings(token))
    config = RemoteFastEnhancerConfig(
        endpoint="localhost:50051",
        api_key=token,
        client_certificate_chain=b"certificate",
        client_private_key=private_key,
    )
    rendered = repr(config)
    assert token not in rendered
    assert private_key.decode() not in rendered


def test_production_model_has_no_cpu_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    with pytest.raises(RuntimeError, match="CPU production inference is forbidden"):
        FastEnhancerBStreamingModel(
            checkpoint_path=Path("missing"),
            manifest_path=Path("missing"),
            config_path=Path("missing"),
        )


@pytest.mark.integration
async def test_mutual_tls_requires_and_accepts_a_client_certificate(tmp_path: Path) -> None:
    authority = trustme.CA()
    server_identity = authority.issue_cert("localhost")
    client_identity = authority.issue_cert("fastenhancer-test-client")
    certificate = tmp_path / "server.crt"
    private_key = tmp_path / "server.private"
    client_ca = tmp_path / "client-ca.crt"
    server_identity.cert_chain_pems[0].write_to_path(certificate)
    server_identity.private_key_pem.write_to_path(private_key)
    authority.cert_pem.write_to_path(client_ca)

    token = "mutual-tls-test-token"  # noqa: S105 -- ephemeral test credential
    config = settings(token)
    config = replace(
        config,
        allow_insecure=False,
        tls_certificate=certificate,
        tls_private_key=private_key,
        tls_client_ca=client_ca,
    )
    metrics = ServerMetrics()
    model = IdentityDelayModel()
    scheduler = BatchScheduler(
        model,
        metrics,
        max_active_streams=config.max_active_streams,
        max_batch_size=config.max_batch_size,
        max_batch_wait_ms=config.max_batch_wait_ms,
    )
    scheduler.start()
    server = grpc.aio.server()
    pb_grpc.add_EnhancementServiceServicer_to_server(
        EnhancementService(model=model, scheduler=scheduler, settings=config, metrics=metrics),
        server,
    )
    port = server.add_secure_port("127.0.0.1:0", _server_credentials(config))
    await server.start()
    metadata = (("authorization", f"Bearer {token}"),)
    options = (("grpc.ssl_target_name_override", "localhost"),)
    try:
        root = authority.cert_pem.bytes()
        without_client = grpc.ssl_channel_credentials(root_certificates=root)
        async with grpc.aio.secure_channel(
            f"127.0.0.1:{port}", without_client, options=options
        ) as channel:
            with pytest.raises(grpc.aio.AioRpcError) as rejected:
                await pb_grpc.EnhancementServiceStub(channel).GetCapabilities(
                    pb.GetCapabilitiesRequest(), metadata=metadata, timeout=1
                )
            assert rejected.value.code() == grpc.StatusCode.UNAVAILABLE

        with_client = grpc.ssl_channel_credentials(
            root_certificates=root,
            private_key=client_identity.private_key_pem.bytes(),
            certificate_chain=client_identity.cert_chain_pems[0].bytes(),
        )
        async with grpc.aio.secure_channel(
            f"127.0.0.1:{port}", with_client, options=options
        ) as channel:
            response = await pb_grpc.EnhancementServiceStub(channel).GetCapabilities(
                pb.GetCapabilitiesRequest(), metadata=metadata, timeout=1
            )
            assert response.capabilities.model_name == "FastEnhancer-B"

        processor = RemoteFastEnhancer(
            f"localhost:{port}",
            api_key=token,
            root_certificates=root,
            client_certificate_chain=client_identity.cert_chain_pems[0].bytes(),
            client_private_key=client_identity.private_key_pem.bytes(),
            response_wait_ms=500,
        )
        try:
            value = rtc.AudioFrame(
                b"\x01\x00" * 512,
                sample_rate=16_000,
                num_channels=1,
                samples_per_channel=512,
            )
            enhanced = await __import__("asyncio").to_thread(processor._process, value)
            assert enhanced.samples_per_channel == 256
        finally:
            await __import__("asyncio").to_thread(processor._close)
    finally:
        await server.stop(0)
        await scheduler.close()
