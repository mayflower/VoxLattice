#!/usr/bin/env python3
"""Fetch the locked FastEnhancer release asset with atomic verification."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

CHUNK_BYTES = 1024 * 1024


class ManifestError(ValueError):
    """The checked-in model manifest is incomplete or malformed."""


class VerificationError(RuntimeError):
    """Downloaded or cached bytes do not match the lock file."""


def _load_manifest(path: Path) -> Mapping[str, object]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read model manifest: {exc}") from exc
    required = {
        "asset_id": int,
        "asset_name": str,
        "asset_url": str,
        "asset_size": int,
        "asset_sha256": str,
    }
    for key, expected_type in required.items():
        value = manifest.get(key)
        if not isinstance(value, expected_type):
            raise ManifestError(f"manifest field {key!r} has the wrong type")
    digest = str(manifest["asset_sha256"])
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ManifestError("asset_sha256 must be 64 lowercase hexadecimal characters")
    if int(manifest["asset_size"]) <= 0 or int(manifest["asset_id"]) <= 0:
        raise ManifestError("asset_size and asset_id must be positive")
    return manifest


def _digest_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def verify(path: Path, *, expected_size: int, expected_sha256: str) -> None:
    if not path.is_file():
        raise VerificationError(f"model archive does not exist: {path}")
    size, digest = _digest_file(path)
    if size != expected_size:
        raise VerificationError(
            f"model archive size mismatch: expected {expected_size}, got {size}"
        )
    if digest != expected_sha256:
        raise VerificationError("model archive SHA-256 mismatch")


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _stream_download(response: BinaryIO, output: BinaryIO) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while chunk := response.read(CHUNK_BYTES):
        output.write(chunk)
        size += len(chunk)
        digest.update(chunk)
    output.flush()
    os.fsync(output.fileno())
    return size, digest.hexdigest()


def fetch(manifest: Mapping[str, object], destination: Path) -> None:
    expected_size = int(manifest["asset_size"])
    expected_sha256 = str(manifest["asset_sha256"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_lock(destination.with_suffix(destination.suffix + ".lock")):
        if destination.exists():
            verify(destination, expected_size=expected_size, expected_sha256=expected_sha256)
            return
        request = urllib.request.Request(  # noqa: S310 -- locked HTTPS GitHub API URL
            str(manifest["asset_url"]),
            headers={
                "Accept": "application/octet-stream",
                "User-Agent": "fastenhancer-model-fetch/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        temporary_name: str | None = None
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) != expected_size:
                    raise VerificationError(
                        "download Content-Length does not match the locked asset size"
                    )
                with tempfile.NamedTemporaryFile(
                    mode="w+b", prefix=f".{destination.name}.", dir=destination.parent, delete=False
                ) as output:
                    temporary_name = output.name
                    size, digest = _stream_download(response, output)
            if size != expected_size:
                raise VerificationError(
                    f"downloaded size mismatch: expected {expected_size}, got {size}"
                )
            if digest != expected_sha256:
                raise VerificationError("downloaded SHA-256 mismatch")
            os.replace(temporary_name, destination)
            temporary_name = None
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("models/manifest.lock.json"))
    parser.add_argument("--output", type=Path, default=Path("models/cache/fastenhancer_b.zip"))
    parser.add_argument("--verify-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        manifest = _load_manifest(args.manifest)
        if args.verify_only:
            verify(
                args.output,
                expected_size=int(manifest["asset_size"]),
                expected_sha256=str(manifest["asset_sha256"]),
            )
        else:
            fetch(manifest, args.output)
    except ManifestError as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 2
    except (OSError, urllib.error.URLError) as exc:
        print(f"model fetch I/O error: {exc}", file=sys.stderr)
        return 3
    except VerificationError as exc:
        print(f"model verification error: {exc}", file=sys.stderr)
        return 4
    print(f"verified model asset {manifest['asset_id']} at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
