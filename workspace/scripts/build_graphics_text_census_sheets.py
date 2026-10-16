#!/usr/bin/env python3
"""Build compact contact sheets for the unreviewed small indexed-graphics census."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

from PIL import Image, ImageDraw


def build(
    review_csv: Path, pack_root: Path, output_root: Path, asset_type: str
) -> int:
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"refusing nonempty output root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    with review_csv.open(encoding="utf-8-sig", newline="") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row["asset_type"] == asset_type
            and int(row["width"]) <= 256
            and int(row["height"]) <= 128
            and int(row["unique_colors"]) <= 64
            and float(row["dominant_fraction"]) < 0.995
        ]
    rows.sort(key=lambda row: (row["archive"], row["asset_id"]))
    columns, page_size = 4, 24
    cell_width, cell_height = 320, 220
    for page_index in range(math.ceil(len(rows) / page_size)):
        page = rows[page_index * page_size : (page_index + 1) * page_size]
        sheet = Image.new(
            "RGB",
            (columns * cell_width, math.ceil(len(page) / columns) * cell_height),
            (28, 28, 28),
        )
        draw = ImageDraw.Draw(sheet)
        for index, row in enumerate(page):
            cell_x = index % columns * cell_width
            cell_y = index // columns * cell_height
            with Image.open(pack_root / row["png_path"]) as opened:
                image = opened.convert("RGB")
            scale = max(1, min(8, 288 // image.width, 176 // image.height))
            image = image.resize(
                (image.width * scale, image.height * scale), Image.Resampling.NEAREST
            )
            sheet.paste(
                image,
                (cell_x + (cell_width - image.width) // 2, cell_y + 4),
            )
            draw.text((cell_x + 5, cell_y + 184), row["asset_id"], fill="white")
            draw.text(
                (cell_x + 5, cell_y + 200),
                f"{row['width']}x{row['height']} {row['bpp']}bpp colors={row['unique_colors']}",
                fill=(255, 220, 96),
            )
        sheet.save(output_root / f"census_{page_index + 1:02d}.png", optimize=False)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-csv", type=Path, required=True)
    parser.add_argument("--pack-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--asset-type", choices=("direct_2d", "model_3d"), default="direct_2d"
    )
    args = parser.parse_args()
    print(build(args.review_csv, args.pack_root, args.output_root, args.asset_type))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
