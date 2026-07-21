#!/usr/bin/env python3
"""Generate protobuf outputs in a temporary directory and compare byte-for-byte."""

from __future__ import annotations

import filecmp
import subprocess
import sys
import tempfile
from pathlib import Path

from generate_proto import replace_generated_placeholders


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    relative_files = (
        Path("fastenhancer/v1/enhancement_pb2.py"),
        Path("fastenhancer/v1/enhancement_pb2.pyi"),
        Path("fastenhancer/v1/enhancement_pb2_grpc.py"),
    )
    with tempfile.TemporaryDirectory(prefix="fastenhancer-proto-") as temporary:
        output = Path(temporary)
        command = [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"--proto_path={root / 'proto'}",
            f"--python_out={output}",
            f"--grpc_python_out={output}",
            f"--pyi_out={output}",
            str(root / "proto/fastenhancer/v1/enhancement.proto"),
        ]
        subprocess.run(command, check=True)  # noqa: S603
        replace_generated_placeholders(output / "fastenhancer/v1/enhancement_pb2_grpc.py")
        drift = [
            str(relative)
            for relative in relative_files
            if not filecmp.cmp(output / relative, root / "generated" / relative, shallow=False)
        ]
    if drift:
        print("protobuf drift: " + ", ".join(drift), file=sys.stderr)
        return 1
    print("protobuf sources match enhancement.proto")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
