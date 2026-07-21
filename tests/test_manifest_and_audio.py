from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest
import torch
import yaml
from fastenhancer_server.audio import PCMRechunker, float32_to_pcm16le, pcm16le_to_float32

ROOT = Path(__file__).resolve().parents[1]


def test_locked_manifest_and_prepared_model() -> None:
    manifest = json.loads((ROOT / "models/manifest.lock.json").read_text())
    assert len(manifest["upstream_commit"]) == 40
    assert manifest["asset_id"] == 302634216
    assert manifest["sample_rate_hz"] == 16_000
    assert manifest["n_fft"] == 512
    assert manifest["hop_samples"] == 256
    assert manifest["algorithmic_delay_samples"] == 256
    checkpoint = ROOT / "models/prepared/00500.pth"
    if checkpoint.exists():
        assert hashlib.sha256(checkpoint.read_bytes()).hexdigest() == manifest["checkpoint_sha256"]
        assert stat.S_IMODE(checkpoint.stat().st_mode) == 0o644


def _dockerfile_instructions() -> list[str]:
    """Dockerfile lines with comments removed, so prose cannot satisfy an assertion."""
    text = (ROOT / "services/fastenhancer-server/Dockerfile").read_text()
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def test_container_runs_module_and_models_are_world_readable() -> None:
    instructions = _dockerfile_instructions()
    joined = "\n".join(instructions)
    copies = [line for line in instructions if line.startswith("COPY")]
    assert 'ENTRYPOINT ["python", "-m", "fastenhancer_server.main"]' in joined
    assert sum(line.count("--chmod=0444") for line in copies) == 2
    assert "models/prepared/00500.pth models/prepared/config.yaml" in joined
    assert "models/manifest.lock.json /opt/model/manifest.lock.json" in joined


def test_container_model_directory_keeps_its_execute_bit() -> None:
    """A COPY --chmod that creates /opt/model implicitly applies the file mode to
    the directory as well. Without the execute bit the checkpoint cannot be opened,
    even by its owner, so the directory is created ahead of the COPY."""
    joined = "\n".join(_dockerfile_instructions())
    assert "install -d -o 65532 -g 65532 -m 0555 /opt/model" in joined


def test_container_builds_against_the_base_image_interpreter() -> None:
    """uv otherwise honours .python-version, downloads a managed interpreter under
    /root, and leaves /opt/venv/bin/python pointing at a path the runtime stage
    never copies."""
    joined = "\n".join(_dockerfile_instructions())
    assert "UV_PYTHON=/usr/local/bin/python" in joined
    assert "UV_PYTHON_DOWNLOADS=never" in joined


def test_local_compose_smoke_profile_selects_an_explicit_gpu() -> None:
    compose = yaml.safe_load((ROOT / "deploy/docker-compose.yml").read_text())
    services = compose["services"]
    devices = services["fastenhancer"]["deploy"]["resources"]["reservations"]["devices"]
    assert devices == [
        {
            "driver": "nvidia",
            "device_ids": [
                "${FASTENHANCER_GPU_DEVICE_ID:?set FASTENHANCER_GPU_DEVICE_ID "
                "to the GPU UUID or index to use}"
            ],
            "capabilities": ["gpu"],
        }
    ]
    smoke = services["smoke"]
    assert smoke["profiles"] == ["test"]
    assert smoke["depends_on"]["fastenhancer"]["condition"] == "service_healthy"
    assert smoke["environment"]["FASTENHANCER_API_TOKEN_FILE"].startswith("/run/secrets/")


def test_pcm_extremes_round_trip() -> None:
    value = bytes.fromhex("0080ffff00000100ff7f")
    decoded = pcm16le_to_float32(value)
    assert decoded.tolist() == pytest.approx([-1.0, -1 / 32768, 0, 1 / 32768, 32767 / 32768])
    assert float32_to_pcm16le(decoded) == value


def test_rechunk_and_partial_flush() -> None:
    rechunker = PCMRechunker(start_sample=1000, max_pending_samples=1024)
    assert rechunker.append(bytes(200)) == []
    hops = rechunker.append(bytes(400))
    assert len(hops) == 1
    assert hops[0].start_sample == 1000
    assert hops[0].valid_samples == 256
    partial = rechunker.flush_partial()
    assert partial is not None
    assert partial.start_sample == 1256
    assert partial.valid_samples == 44
    assert torch.count_nonzero(partial.samples[44:]) == 0


def test_invalid_pcm_rejected() -> None:
    with pytest.raises(ValueError):
        pcm16le_to_float32(b"")
    with pytest.raises(ValueError):
        pcm16le_to_float32(b"x")
