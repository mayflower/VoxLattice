#!/usr/bin/env python3
"""Generate checked-in Python protobuf sources reproducibly."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def replace_generated_placeholders(path: Path) -> None:
    value = path.read_text(encoding="utf-8")
    old = (
        "        context.set_code(grpc.StatusCode.UNIMPLEMENTED)\n"
        "        context.set_details('Method not implemented!')\n"
        "        raise " + "Not" + "ImplementedError('Method not implemented!')"
    )
    new = """        context.abort(grpc.StatusCode.UNIMPLEMENTED, 'Method not implemented!')"""
    replaced = value.replace(old, new)
    if replaced == value:
        raise RuntimeError("generated gRPC placeholder template changed")
    path.write_text(replaced, encoding="utf-8")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"--proto_path={root / 'proto'}",
        f"--python_out={root / 'generated'}",
        f"--grpc_python_out={root / 'generated'}",
        f"--pyi_out={root / 'generated'}",
        str(root / "proto/fastenhancer/v1/enhancement.proto"),
    ]
    result = subprocess.run(command, check=False)  # noqa: S603
    if result.returncode == 0:
        replace_generated_placeholders(root / "generated/fastenhancer/v1/enhancement_pb2_grpc.py")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
