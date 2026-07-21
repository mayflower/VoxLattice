#!/usr/bin/env python3
"""Remove only known, reproducible workspace caches."""

from __future__ import annotations

import shutil
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    names = {"__pycache__", "dist", "build"}
    paths = [root / name for name in (".pytest_cache", ".mypy_cache", ".ruff_cache")]
    scopes = (
        "benchmarks",
        "examples",
        "generated",
        "packages",
        "services",
        "tests",
        "third_party",
        "tools",
    )
    for scope_name in scopes:
        scope = root / scope_name
        if scope.is_dir():
            paths.extend(
                path
                for path in scope.rglob("*")
                if path.is_dir() and (path.name in names or path.name.endswith(".egg-info"))
            )
    for path in sorted(set(paths), key=lambda value: len(value.parts), reverse=True):
        if path.is_dir():
            shutil.rmtree(path)
    print("removed reproducible workspace caches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
