#!/usr/bin/env python3
"""Verify and safely prepare locked members from the model release archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath


def _manifest(path: Path) -> Mapping[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("manifest root must be an object")
    return value


def _read_locked_member(
    archive: zipfile.ZipFile, member: str, expected_size: int, expected_sha256: str
) -> bytes:
    member_path = PurePosixPath(member)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise ValueError("unsafe member path in manifest")
    info = archive.getinfo(member)
    if info.is_dir() or info.file_size != expected_size:
        raise ValueError(f"unexpected archive member size for {member}")
    if info.file_size > 64 * 1024 * 1024:
        raise ValueError("archive member exceeds preparation limit")
    value = archive.read(info)
    if hashlib.sha256(value).hexdigest() != expected_sha256:
        raise ValueError(f"archive member SHA-256 mismatch for {member}")
    return value


def prepare(manifest_path: Path, archive_path: Path, output_dir: Path) -> None:
    manifest = _manifest(manifest_path)
    entries = (
        (
            str(manifest["checkpoint_member"]),
            int(manifest["checkpoint_size"]),
            str(manifest["checkpoint_sha256"]),
            "00500.pth",
        ),
        (
            str(manifest["config_member"]),
            int(manifest["config_size"]),
            str(manifest["config_sha256"]),
            "config.yaml",
        ),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        values = [
            (_read_locked_member(archive, member, size, digest), output_name)
            for member, size, digest, output_name in entries
        ]
    for value, output_name in values:
        target = output_dir / output_name
        fd, temporary = tempfile.mkstemp(prefix=f".{output_name}.", dir=output_dir)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
                os.fchmod(handle.fileno(), 0o644)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("models/manifest.lock.json"))
    parser.add_argument("--archive", type=Path, default=Path("models/cache/fastenhancer_b.zip"))
    parser.add_argument("--output", type=Path, default=Path("models/prepared"))
    args = parser.parse_args()
    prepare(args.manifest, args.archive, args.output)
    print(f"prepared verified model at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
