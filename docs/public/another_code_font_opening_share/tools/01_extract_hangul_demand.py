#!/usr/bin/env python3
"""Extract precomposed Hangul demand and frequency from UTF-8 text files."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def is_hangul(char: str) -> bool:
    return len(char) == 1 and 0xAC00 <= ord(char) <= 0xD7A3


def extract(paths: list[Path]) -> dict[str, object]:
    order: list[str] = []
    counts: Counter[str] = Counter()
    first_source: dict[str, str] = {}
    decoded_bytes = 0
    for path in paths:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
        decoded_bytes += len(raw)
        for char in text:
            if not is_hangul(char):
                continue
            if char not in counts:
                order.append(char)
                first_source[char] = str(path)
            counts[char] += 1
    return {
        "schema_version": 1,
        "input_files": [str(path) for path in paths],
        "input_byte_count": decoded_bytes,
        "unique_hangul_count": len(order),
        "total_hangul_occurrences": sum(counts.values()),
        "first_occurrence_order": "".join(order),
        "characters": [
            {
                "character": char,
                "unicode": f"U+{ord(char):04X}",
                "count": counts[char],
                "first_source": first_source[char],
            }
            for char in order
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-text", type=Path)
    args = parser.parse_args()
    missing = [path for path in args.inputs if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"input files not found: {missing}")
    result = extract(args.inputs)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.output_text:
        args.output_text.parent.mkdir(parents=True, exist_ok=True)
        args.output_text.write_text(
            str(result["first_occurrence_order"]) + "\n", encoding="utf-8"
        )
    print(json.dumps({k: result[k] for k in ("unique_hangul_count", "total_hangul_occurrences")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
