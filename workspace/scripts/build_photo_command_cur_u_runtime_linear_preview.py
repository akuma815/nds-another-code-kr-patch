#!/usr/bin/env python3
"""Build a runtime-linear pda/cur_u replacement while preserving generic tiled import."""

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
SCREEN_CLEAN_BOX = (0, 16, 32, 26)
GLYPH_ORIGIN_Y = 17
PRESERVED_RED_INDICES = {8, 11, 12}
CANONICAL_RED_INDEX = 12
BACKGROUND_INDEX = 15
INK_INDEX = 9


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def unpack_linear_4bpp(data: bytes) -> list[int]:
    if len(data) != WIDTH * HEIGHT // 2:
        raise ValueError("unexpected pda/cur_u texture size")
    values: list[int] = []
    for value in data:
        values.extend((value & 0x0F, value >> 4))
    return values


def pack_linear_4bpp(values: list[int]) -> bytes:
    if len(values) != WIDTH * HEIGHT:
        raise ValueError("unexpected linear pixel count")
    return bytes(values[i] | (values[i + 1] << 4) for i in range(0, len(values), 2))


def flip_vertical(values: list[int]) -> list[int]:
    return [
        values[(HEIGHT - 1 - y) * WIDTH + x]
        for y in range(HEIGHT)
        for x in range(WIDTH)
    ]


def unpack_tiled_4bpp(data: bytes) -> list[int]:
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


def pack_tiled_4bpp(values: list[int]) -> bytes:
    data = bytearray()
    for tile_y in range(0, HEIGHT, 8):
        for tile_x in range(0, WIDTH, 8):
            for y in range(8):
                for pair in range(4):
                    position = (tile_y + y) * WIDTH + tile_x + pair * 2
                    data.append(values[position] | (values[position + 1] << 4))
    return bytes(data)


def bgr555_palette(data: bytes) -> list[int]:
    colors: list[int] = []
    for offset in range(0, len(data), 2):
        value = struct.unpack_from("<H", data, offset)[0]
        colors.extend(((value & 0x1F) * 255 // 31, ((value >> 5) & 0x1F) * 255 // 31, ((value >> 10) & 0x1F) * 255 // 31))
    colors.extend([0, 0, 0] * (256 - len(data) // 2))
    return colors


def indexed_image(values: list[int], palette: bytes) -> Image.Image:
    image = Image.new("P", (WIDTH, HEIGHT))
    image.putdata(values)
    image.putpalette(bgr555_palette(palette))
    image.info["transparency"] = 0
    return image


def checker(image: Image.Image, scale: int = 8) -> Image.Image:
    rgba = image.convert("RGBA").resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)
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
    source_raw = source[0x30:0x230]
    source_runtime = unpack_linear_4bpp(source_raw)
    source_screen = flip_vertical(source_runtime)
    target_screen = list(source_screen)
    x0, y0, x1, y1 = SCREEN_CLEAN_BOX
    preserved: set[int] = set()
    for y in range(y0, y1):
        for x in range(x0, x1):
            position = y * WIDTH + x
            if source_screen[position] in PRESERVED_RED_INDICES:
                preserved.add(position)
                target_screen[position] = CANONICAL_RED_INDEX
            else:
                target_screen[position] = BACKGROUND_INDEX

    display_text = "위로이동"
    for glyph_index, char in enumerate(display_text):
        glyph = load_source_glyph(args.font_png, char)
        for y, glyph_row in enumerate(glyph):
            for x, enabled in enumerate(glyph_row):
                if not enabled:
                    continue
                position = (GLYPH_ORIGIN_Y + y) * WIDTH + glyph_index * 8 + x
                if position not in preserved:
                    target_screen[position] = INK_INDEX

    target_runtime = flip_vertical(target_screen)
    target_raw = pack_linear_4bpp(target_runtime)
    generic_canvas_values = unpack_tiled_4bpp(target_raw)
    if pack_tiled_4bpp(generic_canvas_values) != target_raw:
        raise ValueError("generic tiled canvas does not reconstruct runtime-linear bytes")
    changed = [i for i, pair in enumerate(zip(source_screen, target_screen, strict=True)) if pair[0] != pair[1]]
    if any(not (x0 <= i % WIDTH < x1 and y0 <= i // WIDTH < y1) for i in changed):
        raise ValueError("runtime edit escaped the label row")
    if set(target_screen) - set(source_screen):
        raise ValueError("foreign palette index introduced")
    if any(target_screen[i] != CANONICAL_RED_INDEX for i in preserved):
        raise ValueError("red frame was not normalized to the canonical red index")

    live = args.live_vram_e.read_bytes()
    live_palette = live[args.live_palette_offset:args.live_palette_offset + 0x20]
    if len(live_palette) != 0x20:
        raise ValueError("live palette range unavailable")

    indexed_dir = args.output_root / "indexed"
    preview_dir = args.output_root / "preview_x8"
    indexed_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)
    candidate = indexed_image(generic_canvas_values, source_palette)
    candidate_path = indexed_dir / "pda_cur_u_move_up_ko.png"
    candidate.save(candidate_path)
    source_preview = checker(indexed_image(source_screen, live_palette))
    target_preview = checker(indexed_image(target_screen, live_palette))
    source_preview.save(preview_dir / "pda_cur_u_source_screen_orientation.png")
    target_preview.save(preview_dir / "pda_cur_u_move_up_ko_screen_orientation.png")
    comparison = Image.new("RGB", (source_preview.width, source_preview.height * 2 + 16), "white")
    comparison.paste(source_preview, (0, 0))
    comparison.paste(target_preview, (0, source_preview.height + 16))
    comparison_path = preview_dir / "pda_cur_u_runtime_linear_comparison.png"
    comparison.save(comparison_path)

    report = {
        "schema_version": 1,
        "status": "runtime_linear_png_preview_static_verified_pending_user_approval",
        "resource_id": "pda/cur_u",
        "source_resource": str(args.source_resource),
        "source_sha256": sha256(source),
        "root_cause": "3D texture consumes row-major 4bpp while generic graphics importer consumes a tiled logical PNG",
        "translation": {"source": "上に表示", "korean": "위로 이동", "raster_text": display_text},
        "font": {"path": str(args.font_png), "sha256": sha256(args.font_png.read_bytes())},
        "edit": {
            "screen_clean_box": list(SCREEN_CLEAN_BOX),
            "glyph_origin_y": GLYPH_ORIGIN_Y,
            "vertical_alignment": "centered_with_one_blank_row_above_and_below",
            "screen_orientation": "vertical_flip_of_runtime_linear_texture",
            "changed_runtime_pixel_count": len(changed),
            "changed_outside_screen_clean_box": 0,
            "preserved_red_pixel_count": len(preserved),
            "canonical_red_index": CANONICAL_RED_INDEX,
            "normalized_red_pixel_count": sum(
                source_screen[i] != CANONICAL_RED_INDEX for i in preserved
            ),
            "generic_import_roundtrip": "pass",
            "palette_preserved": True,
        },
        "outputs": {
            "indexed_candidate": str(candidate_path.relative_to(args.output_root)),
            "indexed_candidate_sha256": sha256(candidate_path.read_bytes()),
            "runtime_comparison": str(comparison_path.relative_to(args.output_root)),
            "runtime_comparison_sha256": sha256(comparison_path.read_bytes()),
        },
        "not_proved": ["emulator rendering", "whole-game behavior"],
    }
    (args.output_root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
