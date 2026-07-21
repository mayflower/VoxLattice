#!/usr/bin/env python3
"""Collect reproducible validation evidence without audio or secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
A6000_UUID = "GPU-bac67bca-195d-3490-88f0-b8a3453c5929"
os.environ.setdefault("CUDA_VISIBLE_DEVICES", A6000_UUID)

import torch  # noqa: E402 -- device visibility must be restricted before importing CUDA


def command(args: list[str]) -> dict[str, Any]:
    result = subprocess.run(  # noqa: S603
        args, cwd=ROOT, check=False, capture_output=True, text=True, timeout=1800
    )
    return {
        "command": args,
        "returncode": result.returncode,
        "output": result.stdout + result.stderr,
    }


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--metrics-url", default="http://127.0.0.1:8080/metrics")
    parser.add_argument("--run-tests", action="store_true")
    args = parser.parse_args()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or ROOT / f"artifacts/validation/{timestamp}"
    output.mkdir(parents=True, exist_ok=True)
    gpus = [
        {
            "torch_index": index,
            "name": torch.cuda.get_device_name(index),
            "uuid": str(torch.cuda.get_device_properties(index).uuid),
        }
        for index in range(torch.cuda.device_count())
    ]
    environment = {
        "created_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "uv": command(["uv", "--version"]),
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "gpus": gpus,
        "selected_gpu": next((gpu for gpu in gpus if gpu["name"] == "NVIDIA RTX A6000"), None),
        "docker_server": command(["docker", "version", "--format", "{{.Server.Version}}"]),
    }
    (output / "environment.json").write_text(json.dumps(environment, indent=2) + "\n")
    manifest = json.loads((ROOT / "models/manifest.lock.json").read_text())
    model = {
        "manifest": manifest,
        "archive_sha256": sha256(ROOT / "models/cache/fastenhancer_b.zip"),
        "checkpoint_sha256": sha256(ROOT / "models/prepared/00500.pth"),
        "config_sha256": sha256(ROOT / "models/prepared/config.yaml"),
    }
    (output / "model.json").write_text(json.dumps(model, indent=2) + "\n")
    nvidia = command(
        [
            "nvidia-smi",
            "-i",
            A6000_UUID,
            "--query-gpu=index,uuid,name,driver_version,memory.total",
            "--format=csv",
        ]
    )
    (output / "nvidia-smi.txt").write_text(nvidia["output"])
    docker_inspect = command(["docker", "image", "inspect", "voxlattice:0.1.0"])
    (output / "docker-inspect.json").write_text(json.dumps(docker_inspect, indent=2) + "\n")
    compose_config = command(
        ["docker", "compose", "-f", "deploy/docker-compose.yml", "config", "--quiet"]
    )
    (output / "compose-config.json").write_text(json.dumps(compose_config, indent=2) + "\n")
    try:
        metrics = urllib.request.urlopen(args.metrics_url, timeout=3).read().decode()  # noqa: S310
    except (urllib.error.URLError, TimeoutError) as exc:
        metrics = f"unavailable: {type(exc).__name__}\n"
    (output / "server-metrics.txt").write_text(metrics)
    tests_verified: bool | None = None
    if args.run_tests:
        results = [
            command(["make", target])
            for target in ("check", "test", "test-integration", "test-gpu", "audit")
        ]
        (output / "tests.txt").write_text(
            "\n\n".join(
                f"$ {' '.join(result['command'])}\nexit={result['returncode']}\n{result['output']}"
                for result in results
            )
        )
        tests_verified = all(result["returncode"] == 0 for result in results)
    else:
        (output / "tests.txt").write_text("not requested; run with --run-tests\n")
    summary = {
        "output": str(output),
        "a6000_selected": environment["selected_gpu"] is not None,
        "model_verified": model["checkpoint_sha256"] == manifest["checkpoint_sha256"],
        "compose_config_verified": compose_config["returncode"] == 0,
        "docker_verified": docker_inspect["returncode"] == 0,
        "tests_verified": tests_verified,
    }
    required = (
        summary["a6000_selected"],
        summary["model_verified"],
        summary["compose_config_verified"],
        summary["docker_verified"],
        tests_verified is not False,
    )
    summary["passed"] = all(required)
    (output / "validation.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(output)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
