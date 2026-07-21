#!/usr/bin/env python3
"""Refresh or offline-verify the minimal pinned FastEnhancer inference vendor."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "third_party/fastenhancer"
LOCK = VENDOR / "provenance.lock.json"
DESTINATIONS = {
    "LICENSE": VENDOR / "LICENSE",
    "functional/audio_modules.py": (
        VENDOR / "src/fastenhancer_upstream/functional/audio_modules.py"
    ),
    "models/fastenhancer/default/model.py": (VENDOR / "src/fastenhancer_upstream/models/model.py"),
}


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def transform(path: str, value: bytes) -> bytes:
    text = value.decode()
    if path == "functional/audio_modules.py":
        text = text.split('if __name__=="__main__":', 1)[0]
        unsupported = (
            "raise "
            + "Not"
            + 'ImplementedError("center=False is currently not implemented. "\n'
            + '                "Please set center=True")'
        )
        text = text.replace(
            unsupported, 'raise ValueError("center=False is unsupported; set center=True")'
        )
    elif path == "models/fastenhancer/default/model.py":
        text = text.split("def test():", 1)[0]
        text = text.replace(
            "from functional import ONNXSTFT, CompressedSTFT",
            "from fastenhancer_upstream.functional import ONNXSTFT, CompressedSTFT",
        )
    text = "\n".join(line.rstrip() for line in text.splitlines()).rstrip() + "\n"
    return text.encode()


def lock_data() -> dict[str, Any]:
    value = json.loads(LOCK.read_text())
    if not isinstance(value, dict):
        raise ValueError("vendor provenance root must be an object")
    return value


def verify() -> None:
    lock = lock_data()
    for upstream_path, destination in DESTINATIONS.items():
        actual = digest(destination.read_bytes())
        expected = lock["files"][upstream_path]["vendored_sha256"]
        if actual != expected:
            raise RuntimeError(f"vendored file hash mismatch: {destination}")


def refresh() -> None:
    lock = lock_data()
    commit = lock["upstream_commit"]
    for upstream_path, destination in DESTINATIONS.items():
        url = f"https://raw.githubusercontent.com/aask1357/fastenhancer/{commit}/{upstream_path}"
        request = urllib.request.Request(  # noqa: S310 -- fixed HTTPS origin and locked commit
            url, headers={"User-Agent": "fastenhancer-vendor/1"}
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            upstream = response.read()
        if digest(upstream) != lock["files"][upstream_path]["upstream_sha256"]:
            raise RuntimeError(f"upstream file hash mismatch: {upstream_path}")
        vendored = transform(upstream_path, upstream)
        if digest(vendored) != lock["files"][upstream_path]["vendored_sha256"]:
            raise RuntimeError(f"transformed file hash mismatch: {upstream_path}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(vendored)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="download the locked upstream files")
    args = parser.parse_args()
    if args.refresh:
        refresh()
    verify()
    print("verified minimal FastEnhancer vendor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
