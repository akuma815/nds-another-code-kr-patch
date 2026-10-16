#!/usr/bin/env python3
"""Render palette-independent previews for indexed Another Code pack bitmaps.

Some resources ship with a single-colour placeholder palette even though their
pixel buffers contain many distinct indices.  The game supplies or transforms
the palette at runtime, so the normal palette render looks blank.  This tool
maps the stored indices to greyscale and preserves the selected pixel layout,
making the hidden shapes reviewable without claiming runtime colours.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageDraw

from extract_pack_graphics import arrange_indices, resolve_layout, unpack_indices


def load_indices(data: bytes, bpp: int) -> tuple[int, int, list[int], int]:
    width, height = struct.unpack_from("<HH", data, 4)
    pixel_size, palette_size = struct.unpack_from("<II", data, 8)
    pixel_data = data[16 : 16 + pixel_size]
    indices = unpack_indices(pixel_data, bpp, width * height)
    return width, height, indices, palette_size


def palette_colour_count(data: bytes, indices: list[int], pixel_size: int) -> int:
    palette_size = struct.unpack_from("<I", data, 12)[0]
    colours = []
    start = 16 + pixel_size
    for offset in range(start, start + palette_size, 2):
        colours.append(struct.unpack_from("<H", data, offset)[0] & 0x7FFF)
    return len({colours[index] for index in set(indices)})


def render_index_preview(indices: list[int], width: int, height: int) -> Image.Image:
    used = sorted(set(indices))
    if len(used) <= 1:
        return Image.new("L", (width, height), 0)
    # Rank mapping is robust when the stored indices use only a sparse subset.
    levels = {
        value: round(rank * 255 / (len(used) - 1)) for rank, value in enumerate(used)
    }
    image = Image.new("L", (width, height))
    image.putdata([levels[value] for value in indices])
    return image


def write_contact_sheets(
    output_root: Path, rows: list[dict[str, object]]
) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["archive"])].append(row)
    results: list[dict[str, object]] = []
    columns, page_size = 5, 40
    cell_width, cell_height = 176, 154
    for archive, archive_rows in sorted(grouped.items()):
        for page_index in range(math.ceil(len(archive_rows) / page_size)):
            page = archive_rows[page_index * page_size : (page_index + 1) * page_size]
            sheet_rows = math.ceil(len(page) / columns)
            sheet = Image.new(
                "RGB", (columns * cell_width, sheet_rows * cell_height), (32, 32, 32)
            )
            draw = ImageDraw.Draw(sheet)
            for index, row in enumerate(page):
                x = (index % columns) * cell_width
                y = (index // columns) * cell_height
                with Image.open(output_root / str(row["preview_path"])) as source:
                    thumb = source.convert("RGB")
                    thumb.thumbnail((160, 120), Image.Resampling.NEAREST)
                sheet.paste(thumb, (x + (cell_width - thumb.width) // 2, y + 4))
                draw.text((x + 6, y + 128), str(row["resource_id"]), fill=(255, 255, 255))
                draw.text(
                    (x + 6, y + 140),
                    f"idx={row['index_count']} pal={row['rendered_colour_count']}",
                    fill=(255, 220, 96),
                )
            rel = Path("contact_sheets") / f"{archive}_{page_index + 1:02d}.png"
            target = output_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            sheet.save(target, format="PNG", optimize=False)
            results.append(
                {
                    "archive": archive,
                    "page": page_index + 1,
                    "count": len(page),
                    "path": str(rel),
                }
            )
    return results


def build(pack_root: Path, output_root: Path, collapsed_only: bool) -> dict[str, object]:
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty output root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    report = json.loads((pack_root / "report.json").read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    for resource in report["resources"]:
        if resource.get("format") not in ("indexed4_bgr555", "indexed8_bgr555"):
            continue
        data = (pack_root / str(resource["decoded_path"])).read_bytes()
        width, height, indices, _ = load_indices(data, int(resource["bpp"]))
        index_count = len(set(indices))
        pixel_size = struct.unpack_from("<I", data, 8)[0]
        rendered_colour_count = palette_colour_count(data, indices, pixel_size)
        if index_count <= 1:
            continue
        if collapsed_only and rendered_colour_count > 1:
            continue
        layout = resolve_layout(str(resource.get("selected_layout") or "auto"), width, height)
        arranged = arrange_indices(indices, width, height, layout)
        rel = Path("png") / str(resource["archive"]) / f"{resource['resource']}.png"
        target = output_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with render_index_preview(arranged, width, height) as image:
            image.save(target, format="PNG", optimize=False)
        rows.append(
            {
                "resource_id": resource["resource_id"],
                "archive": resource["archive"],
                "resource": resource["resource"],
                "width": width,
                "height": height,
                "bpp": resource["bpp"],
                "layout": layout,
                "index_count": index_count,
                "rendered_colour_count": rendered_colour_count,
                "preview_path": str(rel),
                "state": "diagnostic_index_rank_preview_not_runtime_colour",
            }
        )
    sheets = write_contact_sheets(output_root, rows)
    result = {
        "schema_version": 1,
        "status": "palette_independent_index_previews_ready",
        "pack_root": str(pack_root),
        "collapsed_only": collapsed_only,
        "resource_count": len(rows),
        "archive_counts": dict(sorted(Counter(str(r["archive"]) for r in rows).items())),
        "contact_sheets": sheets,
        "rows": rows,
        "not_proved": [
            "runtime palette colours",
            "runtime layer composition",
            "Japanese text presence without visual review",
            "graphics reinsertion compatibility",
        ],
    }
    (output_root / "report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--all", action="store_true", help="include non-collapsed palettes")
    args = parser.parse_args()
    result = build(args.pack_root, args.output_root, collapsed_only=not args.all)
    print(json.dumps({k: result[k] for k in ("status", "resource_count", "archive_counts")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
