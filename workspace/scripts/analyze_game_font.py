#!/usr/bin/env python3
"""Audit Another Code's fixed 16x16 2bpp Shift-JIS font table."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


GLYPH_SIZE = 64
TRAILS_PER_LEAD = 188


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def index_to_sjis(index: int) -> int:
    lead_index, trail_index = divmod(index, TRAILS_PER_LEAD)
    lead = 0x81 + lead_index if lead_index <= 0x1E else 0xC1 + lead_index
    trail = 0x40 + trail_index
    if trail >= 0x7F:
        trail += 1
    return (lead << 8) | trail


def sjis_to_index(code: int) -> int:
    lead = code >> 8
    trail = code & 0xFF
    if 0x81 <= lead <= 0x9F:
        lead_index = lead - 0x81
    elif 0xE0 <= lead <= 0xEF:
        lead_index = lead - 0xC1
    else:
        raise ValueError(f"Unsupported Shift-JIS lead byte: 0x{lead:02X}")
    if not (0x40 <= trail <= 0xFC) or trail == 0x7F:
        raise ValueError(f"Unsupported Shift-JIS trail byte: 0x{trail:02X}")
    trail_index = trail - 0x40 - (1 if trail >= 0x80 else 0)
    return lead_index * TRAILS_PER_LEAD + trail_index


def changed_glyphs(base: bytes, candidate: bytes) -> list[int]:
    if len(base) != len(candidate) or len(base) % GLYPH_SIZE:
        raise ValueError("Font inputs must have the same size divisible by 64")
    return [
        index
        for index in range(len(base) // GLYPH_SIZE)
        if base[index * GLYPH_SIZE : (index + 1) * GLYPH_SIZE]
        != candidate[index * GLYPH_SIZE : (index + 1) * GLYPH_SIZE]
    ]


def ranges(values: list[int]) -> list[dict[str, int | str]]:
    if not values:
        return []
    result = []
    start = previous = values[0]
    for value in values[1:]:
        if value != previous + 1:
            result.append(
                {
                    "start_index": start,
                    "end_index": previous,
                    "start_sjis": f"{index_to_sjis(start):04X}",
                    "end_sjis": f"{index_to_sjis(previous):04X}",
                }
            )
            start = value
        previous = value
    result.append(
        {
            "start_index": start,
            "end_index": previous,
            "start_sjis": f"{index_to_sjis(start):04X}",
            "end_sjis": f"{index_to_sjis(previous):04X}",
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    base = args.base.read_bytes()
    if len(base) % GLYPH_SIZE:
        raise ValueError("Base font size is not divisible by the 64-byte glyph size")
    report = {
        "schema_version": 1,
        "base": {"path": str(args.base), "size": len(base), "sha256": sha256(base)},
        "format": {
            "glyph_width": 16,
            "glyph_height": 16,
            "bits_per_pixel": 2,
            "bytes_per_glyph": GLYPH_SIZE,
            "tile_layout": "four 8x8 tiles in top-left, top-right, bottom-left, bottom-right order",
            "tile_pixel_order": "four packed 2-bit pixels per byte, most-significant pair first",
            "glyph_count": len(base) // GLYPH_SIZE,
            "code_index": "Shift-JIS lead/trail linear index with 188 trails per lead",
        },
    }
    if args.candidate:
        candidate = args.candidate.read_bytes()
        changed = changed_glyphs(base, candidate)
        report["candidate"] = {
            "path": str(args.candidate),
            "size": len(candidate),
            "sha256": sha256(candidate),
        }
        report["comparison"] = {
            "changed_glyph_count": len(changed),
            "changed_glyph_indices": changed,
            "changed_sjis_codes": [f"{index_to_sjis(index):04X}" for index in changed],
            "changed_ranges": ranges(changed),
        }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
