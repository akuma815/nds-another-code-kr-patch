#!/usr/bin/env python3
"""Convert a validated JSON Hangul mapping to a CODE=CHAR table file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mapping", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    source = json.loads(args.mapping.read_text(encoding="utf-8-sig"))
    mappings = source.get("mappings")
    if not isinstance(mappings, list) or not mappings:
        raise ValueError("mapping JSON must contain a nonempty mappings list")
    seen_chars: set[str] = set()
    seen_codes: set[int] = set()
    lines = []
    for number, row in enumerate(mappings, 1):
        char = str(row.get("character", ""))
        if len(char) != 1:
            raise ValueError(f"row {number}: character must contain exactly one code point")
        code = int(str(row.get("code", "")), 16)
        if not 0 <= code <= 0xFFFF:
            raise ValueError(f"row {number}: code is outside two-byte range")
        if char in seen_chars or code in seen_codes:
            raise ValueError(f"row {number}: duplicate character or code")
        seen_chars.add(char)
        seen_codes.add(code)
        lines.append(f"{code:04X}={char}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(lines)} mappings to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
