#!/usr/bin/env python3
"""Validate relative links in user-facing Markdown documentation."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_FILES = (
    *ROOT.glob("*.md"),
    *ROOT.glob("docs/**/*.md"),
    *ROOT.glob("models/*.md"),
    *ROOT.glob("examples/**/*.md"),
    *ROOT.glob("packages/**/README.md"),
    *ROOT.glob("services/**/README.md"),
    *ROOT.glob("generated/README.md"),
    *ROOT.glob("third_party/**/*.md"),
)
LINK = re.compile(r"\]\(([^)]+)\)")


def main() -> int:
    errors: list[str] = []
    for path in sorted(set(MARKDOWN_FILES)):
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for raw_target in LINK.findall(line):
                target = raw_target.strip().strip("<>").split("#", 1)[0]
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                resolved = (path.parent / unquote(target)).resolve()
                if not resolved.is_relative_to(ROOT) or not resolved.exists():
                    relative = path.relative_to(ROOT)
                    errors.append(f"{relative}:{line_number}: broken relative link: {raw_target}")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"validated relative links in {len(set(MARKDOWN_FILES))} Markdown files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
