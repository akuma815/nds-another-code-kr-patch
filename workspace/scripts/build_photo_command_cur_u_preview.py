#!/usr/bin/env python3
"""Build a PNG-only Galmuri7 preview for the live-traced pda/cur_u label."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct

from PIL import Image

from build_font_poc import load_source_glyph


SOURCE_SHA256 = "58A1528C6670B90F18E144BEC390379F8802755F103170751B2F11556938D9B2"
WIDTH = 32
HEIGHT = 32
TEXT_BOX = (0, 8, 32, 16)
PRESERVED_RED_INDICES = {8, 11, 12}
BACKGROUND_INDEX = 15
INK_INDEX = 9


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def unpack_tiled_4bpp(data: bytes) -> list[int]:
    if len(data) != WIDTH * HEIGHT // 2:
        raise ValueError("unexpected pda/cur_u texture size")
    pixels = [0] * (WIDTH * HEIGHT)
    tile_offset = 0
    for tile_y in range(0, HEIGHT, 8):
        for tile_x in range(0, WIDTH, 8):
            for y in range(8):
                for pair in range(4):
                    value = data[tile_offset]
                    tile_offset += 1
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


def checker(image: Image.Image, scale: int = 8) -> Image.Image:
    rgba = image.convert("RGBA").resize(
        (image.width * scale, image.height * scale), Image.Resampling.NEAREST
    )
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
    parser.add_argument("--live-vram-e", type=Path, required=True)
    parser.add_argument("--live-palette-offset", type=lambda value: int(value, 0), default=0x740)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    if args.output_root.exists() and any(args.output_root.iterdir()):
        raise FileExistsError(f"refusing nonempty output root: {args.output_root}")

    source = args.source_resource.read_bytes()
    if sha256(source) != SOURCE_SHA256:
        raise ValueError("pda/cur_u source hash mismatch")
    envelope = struct.unpack_from("<IHHII", source, 0)
    if envelope != (0x00100000, WIDTH, HEIGHT, 0x200, 0x20):
        raise ValueError(f"unexpected pda/cur_u envelope: {envelope}")

    source_palette = source[0x10:0x30]
    source_values = unpack_tiled_4bpp(source[0x30:])
    values = list(source_values)

    x0, y0, x1, y1 = TEXT_BOX
    preserved = []
    erased = []
    for y in range(y0, y1):
        for x in range(x0, x1):
            position = y * WIDTH + x
            if source_values[position] in PRESERVED_RED_INDICES:
                preserved.append(position)
            else:
                if source_values[position] != BACKGROUND_INDEX:
                    erased.append(position)
                values[position] = BACKGROUND_INDEX

    display_text = "위에표시"
    for glyph_index, char in enumerate(display_text):
        glyph = load_source_glyph(args.font_png, char)
        for y, row in enumerate(glyph):
            for x, enabled in enumerate(row):
                if not enabled:
                    continue
                position = (y0 + y) * WIDTH + glyph_index * 8 + x
                if position not in preserved:
                    values[position] = INK_INDEX

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
    if set(values) - set(source_values):
        raise ValueError("preview introduced a foreign palette index")
    if any(values[position] != source_values[position] for position in preserved):
        raise ValueError("preview changed a protected red arrow/frame pixel")

    live_vram_e = args.live_vram_e.read_bytes()
    palette_end = args.live_palette_offset + 0x20
    if palette_end > len(live_vram_e):
        raise ValueError("live palette range exceeds VRAM-E dump")
    live_palette = live_vram_e[args.live_palette_offset:palette_end]

    indexed_dir = args.output_root / "indexed"
    preview_dir = args.output_root / "preview_x8"
    indexed_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    source_indexed = indexed_image(source_values, source_palette)
    candidate_indexed = indexed_image(values, source_palette)
    source_live = indexed_image(source_values, live_palette)
    candidate_live = indexed_image(values, live_palette)

    source_png = indexed_dir / "pda_cur_u_source.png"
    candidate_png = indexed_dir / "pda_cur_u_up_display_ko.png"
    source_live_png = preview_dir / "pda_cur_u_source_live_palette.png"
    candidate_live_png = preview_dir / "pda_cur_u_up_display_ko_live_palette.png"
    comparison_png = preview_dir / "pda_cur_u_up_display_comparison.png"
    source_indexed.save(source_png)
    candidate_indexed.save(candidate_png)
    source_preview = checker(source_live)
    candidate_preview = checker(candidate_live)
    source_preview.save(source_live_png)
    candidate_preview.save(candidate_live_png)
    comparison = Image.new("RGB", (source_preview.width, source_preview.height * 2 + 16), "white")
    comparison.paste(source_preview, (0, 0))
    comparison.paste(candidate_preview, (0, source_preview.height + 16))
    comparison.save(comparison_png)

    report = {
        "schema_version": 1,
        "status": "png_preview_only_pending_user_approval",
        "resource_id": "pda/cur_u",
        "runtime_evidence": {
            "render_polygon": 0,
            "screen_bounds": [112, 0, 144, 32],
            "vram_texture_address": "0x25E00",
            "vram_bank": "B",
            "vram_bank_offset": "0x5E00",
            "texture_exact_match_offset": "0x30",
        },
        "source_resource": str(args.source_resource),
        "source_sha256": sha256(source),
        "translation": {"source": "上に表示", "korean": "위에 표시", "raster_text": display_text},
        "font": {"path": str(args.font_png), "sha256": sha256(args.font_png.read_bytes())},
        "edit": {
            "text_box": list(TEXT_BOX),
            "background_index": BACKGROUND_INDEX,
            "replacement_ink_index": INK_INDEX,
            "preserved_red_indices": sorted(PRESERVED_RED_INDICES),
            "preserved_red_pixel_count": len(preserved),
            "erased_nonbackground_pixel_count": len(erased),
            "changed_pixel_count": len(changed),
            "changed_outside_text_box": 0,
            "protected_pixels_preserved": True,
        },
        "live_palette_evidence": {
            "path": str(args.live_vram_e),
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
    report_path = args.output_root / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
