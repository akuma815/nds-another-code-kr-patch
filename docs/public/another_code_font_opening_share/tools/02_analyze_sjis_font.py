#!/usr/bin/env python3
"""Analyze and render a 16x16, 2bpp, Shift-JIS-indexed font binary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image


GLYPH_SIZE = 64
TRAILS_PER_LEAD = 188


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def index_to_code(index: int) -> int:
    lead_index, trail_index = divmod(index, TRAILS_PER_LEAD)
    lead = 0x81 + lead_index if lead_index <= 0x1E else 0xC1 + lead_index
    trail = 0x40 + trail_index
    if trail >= 0x7F:
        trail += 1
    return (lead << 8) | trail


def unpack_glyph(data: bytes) -> list[int]:
    if len(data) != GLYPH_SIZE:
        raise ValueError("glyph must be exactly 64 bytes")
    pixels = [0] * 256
    cursor = 0
    for tile_y, tile_x in ((0, 0), (0, 8), (8, 0), (8, 8)):
        for y in range(8):
            for group in range(2):
                value = data[cursor]
                cursor += 1
                for pixel in range(4):
                    pixels[(tile_y + y) * 16 + tile_x + group * 4 + pixel] = (
                        value >> (6 - pixel * 2)
                    ) & 3
    return pixels


def build_atlas(data: bytes, columns: int, scale: int) -> Image.Image:
    count = len(data) // GLYPH_SIZE
    rows = (count + columns - 1) // columns
    image = Image.new("L", (columns * 16, rows * 16), 255)
    for index in range(count):
        values = unpack_glyph(data[index * GLYPH_SIZE : (index + 1) * GLYPH_SIZE])
        glyph = Image.new("L", (16, 16))
        glyph.putdata([255 - value * 85 for value in values])
        image.paste(glyph, ((index % columns) * 16, (index // columns) * 16))
    if scale != 1:
        image = image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("font", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--atlas", type=Path)
    parser.add_argument("--columns", type=int, default=64)
    parser.add_argument("--scale", type=int, default=1)
    args = parser.parse_args()
    data = args.font.read_bytes()
    if not data or len(data) % GLYPH_SIZE:
        raise ValueError("font size must be a nonzero multiple of 64 bytes")
    if args.columns < 1 or args.scale < 1:
        raise ValueError("columns and scale must be positive")
    glyph_count = len(data) // GLYPH_SIZE
    zero_indices = [
        index
        for index in range(glyph_count)
        if not any(data[index * GLYPH_SIZE : (index + 1) * GLYPH_SIZE])
    ]
    report = {
        "schema_version": 1,
        "font": {"path": str(args.font), "size": len(data), "sha256": sha256(data)},
        "format_assumption": {
            "width": 16,
            "height": 16,
            "bits_per_pixel": 2,
            "bytes_per_glyph": GLYPH_SIZE,
            "tile_order": ["top_left", "top_right", "bottom_left", "bottom_right"],
            "trails_per_lead": TRAILS_PER_LEAD,
        },
        "glyph_count": glyph_count,
        "all_zero_glyph_count": len(zero_indices),
        "nonzero_glyph_count": glyph_count - len(zero_indices),
        "all_zero_glyph_indices": zero_indices,
        "all_zero_codes": [f"{index_to_code(index):04X}" for index in zero_indices],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.atlas:
        args.atlas.parent.mkdir(parents=True, exist_ok=True)
        with build_atlas(data, args.columns, args.scale) as atlas:
            atlas.save(args.atlas, format="PNG", optimize=False)
    print(json.dumps({"glyph_count": glyph_count, "all_zero_glyph_count": len(zero_indices)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
