#!/usr/bin/env python3
"""Build a PNG-only preview for the live-traced sys_bg/myst00_bg back button."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct

from PIL import Image

from build_font_poc import load_source_glyph


SOURCE_SHA256 = "985E7D0C61C942310EC7D27D2BABC12788C708DBB6CB58D52F01F25951F18953"
WIDTH = 32
HEIGHT = 40
VISIBLE_HEIGHT = 32
TEXT_BOX = (4, 11, 28, 19)
INK_INDEX = 15


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def unpack_tiled_4bpp(data: bytes) -> list[int]:
    if len(data) != WIDTH * HEIGHT // 2:
        raise ValueError("unexpected sys_bg/myst00_bg texture size")
    pixels = [0] * (WIDTH * HEIGHT)
    offset = 0
    for tile_y in range(0, HEIGHT, 8):
        for tile_x in range(0, WIDTH, 8):
            for y in range(8):
                for pair in range(4):
                    value = data[offset]
                    offset += 1
                    position = (tile_y + y) * WIDTH + tile_x + pair * 2
                    pixels[position] = value & 0x0F
                    pixels[position + 1] = value >> 4
    return pixels


def bgr555_palette(data: bytes) -> list[int]:
    colors: list[int] = []
    for offset in range(0, len(data), 2):
        value = struct.unpack_from("<H", data, offset)[0]
        colors.extend(
            (
                (value & 0x1F) * 255 // 31,
                ((value >> 5) & 0x1F) * 255 // 31,
                ((value >> 10) & 0x1F) * 255 // 31,
            )
        )
    colors.extend([0, 0, 0] * (256 - len(data) // 2))
    return colors


def indexed_image(values: list[int], palette: bytes) -> Image.Image:
    image = Image.new("P", (WIDTH, HEIGHT))
    image.putdata(values)
    image.putpalette(bgr555_palette(palette))
    image.info["transparency"] = 0
    return image


def checker_visible(image: Image.Image, scale: int = 8) -> Image.Image:
    visible = image.crop((0, 0, WIDTH, VISIBLE_HEIGHT)).convert("RGBA")
    rgba = visible.resize((WIDTH * scale, VISIBLE_HEIGHT * scale), Image.Resampling.NEAREST)
    background = Image.new("RGBA", rgba.size)
    pixels = background.load()
    assert pixels is not None
    for y in range(rgba.height):
        for x in range(rgba.width):
            shade = 184 if ((x // 16) + (y // 16)) % 2 else 224
            pixels[x, y] = (shade, shade, shade, 255)
    background.alpha_composite(rgba)
    return background.convert("RGB")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-resource", type=Path, required=True)
    parser.add_argument("--font-png", type=Path, required=True)
    parser.add_argument("--live-palette", type=Path, required=True)
    parser.add_argument("--live-palette-offset", type=lambda value: int(value, 0), default=0x300)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    if args.output_root.exists() and any(args.output_root.iterdir()):
        raise FileExistsError(f"refusing nonempty output root: {args.output_root}")

    source = args.source_resource.read_bytes()
    if sha256(source) != SOURCE_SHA256:
        raise ValueError("sys_bg/myst00_bg source hash mismatch")
    envelope = struct.unpack_from("<IHHII", source, 0)
    if envelope != (0x00100000, WIDTH, HEIGHT, 0x280, 0x20):
        raise ValueError(f"unexpected sys_bg/myst00_bg envelope: {envelope}")

    source_palette = source[0x10:0x30]
    source_values = unpack_tiled_4bpp(source[0x30:])
    values = list(source_values)
    x0, y0, x1, y1 = TEXT_BOX

    restored: list[int] = []
    row_backgrounds: list[int] = []
    for y in range(y0, y1):
        # x=27 is the untouched right-side interior column on every text row.
        # Sampling inside the old label made its gray antialias index become the
        # row background at y=18, leaving a visible gray strip below 뒤로.
        background = source_values[y * WIDTH + 27]
        row_backgrounds.append(background)
        for x in range(x0, x1):
            position = y * WIDTH + x
            if source_values[position] != background:
                values[position] = background
                restored.append(position)

    display_text = "뒤로"
    start_x = 8
    for glyph_index, char in enumerate(display_text):
        glyph = load_source_glyph(args.font_png, char)
        for y, row in enumerate(glyph):
            for x, enabled in enumerate(row):
                if enabled:
                    values[(y0 + y) * WIDTH + start_x + glyph_index * 8 + x] = INK_INDEX

    changed = [
        index
        for index, (before, after) in enumerate(zip(source_values, values, strict=True))
        if before != after
    ]
    escaped = [
        index
        for index in changed
        if not (x0 <= index % WIDTH < x1 and y0 <= index // WIDTH < y1)
    ]
    if escaped:
        raise ValueError(f"preview changed protected pixels: {escaped[:8]}")
    if any(values[index] != source_values[index] for index in range(WIDTH * VISIBLE_HEIGHT, WIDTH * HEIGHT)):
        raise ValueError("preview changed hidden rows 32..39")

    palette_dump = args.live_palette.read_bytes()
    palette_end = args.live_palette_offset + 0x20
    if palette_end > len(palette_dump):
        raise ValueError("live palette range exceeds dump")
    live_palette = palette_dump[args.live_palette_offset:palette_end]

    indexed_dir = args.output_root / "indexed"
    preview_dir = args.output_root / "preview_x8"
    indexed_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    source_indexed = indexed_image(source_values, source_palette)
    candidate_indexed = indexed_image(values, source_palette)
    source_live = indexed_image(source_values, live_palette)
    candidate_live = indexed_image(values, live_palette)
    source_png = indexed_dir / "myst00_bg_source.png"
    candidate_png = indexed_dir / "myst00_bg_back_ko.png"
    source_preview_png = preview_dir / "myst00_bg_source_live_palette.png"
    candidate_preview_png = preview_dir / "myst00_bg_back_ko_live_palette.png"
    comparison_png = preview_dir / "myst00_bg_back_comparison.png"
    source_indexed.save(source_png)
    candidate_indexed.save(candidate_png)
    source_preview = checker_visible(source_live)
    candidate_preview = checker_visible(candidate_live)
    source_preview.save(source_preview_png)
    candidate_preview.save(candidate_preview_png)
    comparison = Image.new("RGB", (source_preview.width, source_preview.height * 2 + 16), "white")
    comparison.paste(source_preview, (0, 0))
    comparison.paste(candidate_preview, (0, source_preview.height + 16))
    comparison.save(comparison_png)

    report = {
        "schema_version": 1,
        "status": "png_preview_only_pending_user_approval",
        "resource_id": "sys_bg/myst00_bg",
        "runtime_evidence": {
            "process_id": 23992,
            "oam_entry": 99,
            "screen_bounds": [224, 161, 32, 32],
            "vram_bank": "F",
            "vram_bank_offset": "0x3200",
            "decoded_exact_match_offset": "0x30",
        },
        "source_resource": str(args.source_resource),
        "source_sha256": sha256(source),
        "translation": {"source": "もどる", "korean": "뒤로", "raster_text": display_text},
        "font": {"path": str(args.font_png), "sha256": sha256(args.font_png.read_bytes())},
        "edit": {
            "text_box": list(TEXT_BOX),
            "replacement_ink_index": INK_INDEX,
            "row_background_indices": row_backgrounds,
            "restored_text_area_pixel_count": len(restored),
            "changed_pixel_count": len(changed),
            "changed_outside_text_box": 0,
            "hidden_rows_preserved": True,
        },
        "live_palette_evidence": {
            "path": str(args.live_palette),
            "offset": f"0x{args.live_palette_offset:X}",
            "sha256": sha256(live_palette),
            "preview_only": True,
        },
        "outputs": {
            "indexed_candidate": str(candidate_png.relative_to(args.output_root)),
            "indexed_candidate_sha256": sha256(candidate_png.read_bytes()),
            "comparison": str(comparison_png.relative_to(args.output_root)),
            "comparison_sha256": sha256(comparison_png.read_bytes()),
        },
        "safety": {"decoded_binary_written": False, "rom_built": False},
    }
    (args.output_root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
