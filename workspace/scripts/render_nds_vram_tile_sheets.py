#!/usr/bin/env python3
"""Render physical NDS VRAM dumps as palette-independent tile sheets."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from PIL import Image


def decode_tile(data: bytes, bpp: int) -> list[int]:
    if bpp == 2:
        return [(byte >> shift) & 3 for byte in data for shift in (0, 2, 4, 6)]
    if bpp == 4:
        return [(byte >> shift) & 15 for byte in data for shift in (0, 4)]
    if bpp == 8:
        return list(data)
    raise ValueError(bpp)


def render(data: bytes, bpp: int, columns: int, mode: str) -> Image.Image:
    tile_size = 8 * 8 * bpp // 8
    tile_count = len(data) // tile_size
    rows = (tile_count + columns - 1) // columns
    image = Image.new("L", (columns * 8, rows * 8), 255)
    pixels = image.load()
    max_value = (1 << bpp) - 1
    for tile_index in range(tile_count):
        values = decode_tile(
            data[tile_index * tile_size : (tile_index + 1) * tile_size], bpp
        )
        if mode == "index":
            ink = [255 - round(value * 255 / max_value) for value in values]
        else:
            background = Counter(values).most_common(1)[0][0]
            others = sorted(set(values) - {background})
            shades = {background: 255}
            for rank, value in enumerate(others):
                shades[value] = max(0, 48 - rank * 16)
            ink = [shades[value] for value in values]
        tile_x = (tile_index % columns) * 8
        tile_y = (tile_index // columns) * 8
        for pos, value in enumerate(ink):
            pixels[tile_x + pos % 8, tile_y + pos // 8] = value
    return image


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--columns", type=int, default=32)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(args.dump_dir.glob("vram_[A-I]_*.bin")):
        data = path.read_bytes()
        for bpp in (2, 4, 8):
            for mode in ("index", "local_contrast"):
                output = render(data, bpp, args.columns, mode)
                output.save(args.out_dir / f"{path.stem}_{bpp}bpp_{mode}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
