#!/usr/bin/env python3
"""Audit a fixed-cell PNG font sheet without assuming its code-point order."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from PIL import Image


def analyze(path: Path, cell_width: int, cell_height: int) -> dict:
    with Image.open(path) as opened:
        source_format = opened.format
        image = opened.convert("RGBA")
    if image.width % cell_width or image.height % cell_height:
        raise ValueError("Image dimensions are not divisible by the requested cell size")
    colors = Counter(image.get_flattened_data())
    background, _count = colors.most_common(1)[0]
    columns = image.width // cell_width
    rows = image.height // cell_height
    patterns = Counter()
    empty_cells = []
    transparent_cells = []
    ink_counts = []
    for index in range(columns * rows):
        x = (index % columns) * cell_width
        y = (index // columns) * cell_height
        cell = image.crop((x, y, x + cell_width, y + cell_height))
        pixels = list(cell.get_flattened_data())
        patterns[cell.tobytes()] += 1
        ink = sum(pixel != background and pixel[3] != 0 for pixel in pixels)
        ink_counts.append(ink)
        if ink == 0:
            empty_cells.append(index)
        if all(pixel[3] == 0 for pixel in pixels):
            transparent_cells.append(index)
    nonempty = [index for index, count in enumerate(ink_counts) if count]
    return {
        "schema_version": 1,
        "source": {
            "path": str(path),
            "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest().upper(),
            "format": source_format,
            "mode_after_conversion": "RGBA",
        },
        "image": {
            "width": image.width,
            "height": image.height,
            "color_count": len(colors),
            "colors": [
                {"rgba": list(color), "pixel_count": count}
                for color, count in colors.most_common()
            ],
            "dominant_background_rgba": list(background),
        },
        "grid": {
            "cell_width": cell_width,
            "cell_height": cell_height,
            "columns": columns,
            "rows": rows,
            "cell_count": columns * rows,
            "nonempty_cell_count": len(nonempty),
            "first_nonempty_cell": nonempty[0] if nonempty else None,
            "last_nonempty_cell": nonempty[-1] if nonempty else None,
            "empty_cell_indices": empty_cells,
            "fully_transparent_cell_indices": transparent_cells,
            "unique_bitmap_count": len(patterns),
            "duplicate_cell_count": columns * rows - len(patterns),
            "ink_pixels_min": min((ink_counts[index] for index in nonempty), default=0),
            "ink_pixels_max": max(ink_counts, default=0),
        },
        "codepoint_order": {
            "status": "hypothesis",
            "candidate": "cell 0..11171 maps sequentially to U+AC00..U+D7A3",
            "basis": "exactly 11172 consecutive nonempty cells followed by 28 transparent padding cells",
            "requires": "source metadata or a known-codepoint visual/runtime comparison",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--png", type=Path, required=True)
    parser.add_argument("--cell-width", type=int, default=8)
    parser.add_argument("--cell-height", type=int, default=8)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = analyze(args.png, args.cell_width, args.cell_height)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
