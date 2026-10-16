#!/usr/bin/env python3
"""Build a PNG-only preview for the 3D sys_bg/myst00_tex return button."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct

from PIL import Image

from build_font_poc import load_source_glyph


SOURCE_SHA256 = "165F6C62E4FDFBE467FC16AABAB351B41831E407D736C916CE806F6720C50585"
WIDTH = 32
HEIGHT = 32
TEXT_BOX = (4, 13, 28, 23)
INK_INDEX = 15


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def unpack_linear_4bpp(data: bytes) -> list[int]:
    if len(data) != WIDTH * HEIGHT // 2:
        raise ValueError("unexpected sys_bg/myst00_tex texture size")
    values: list[int] = []
    for value in data:
        values.extend((value & 0x0F, value >> 4))
    return values


def palette_rgb(data: bytes) -> list[int]:
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
    return colors + [0, 0, 0] * (256 - len(data) // 2)


def indexed_image(values: list[int], palette: bytes) -> Image.Image:
    image = Image.new("P", (WIDTH, HEIGHT))
    image.putdata(values)
    image.putpalette(palette_rgb(palette))
    image.info["transparency"] = 0
    return image


def checker_preview(image: Image.Image, scale: int = 8) -> Image.Image:
    enlarged = image.convert("RGBA").resize(
        (WIDTH * scale, HEIGHT * scale), Image.Resampling.NEAREST
    )
    checker = Image.new("RGBA", enlarged.size)
    pixels = checker.load()
    assert pixels is not None
    for y in range(enlarged.height):
        for x in range(enlarged.width):
            shade = 184 if ((x // 16) + (y // 16)) % 2 else 224
            pixels[x, y] = (shade, shade, shade, 255)
    checker.alpha_composite(enlarged)
    return checker.convert("RGB")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-resource", type=Path, required=True)
    parser.add_argument("--font-png", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    if args.output_root.exists() and any(args.output_root.iterdir()):
        raise FileExistsError(f"refusing nonempty output root: {args.output_root}")

    source = args.source_resource.read_bytes()
    if sha256(source) != SOURCE_SHA256:
        raise ValueError("sys_bg/myst00_tex source hash mismatch")
    envelope = struct.unpack_from("<IHHII", source, 0)
    if envelope != (0x00100000, WIDTH, HEIGHT, 0x200, 0x20):
        raise ValueError(f"unexpected sys_bg/myst00_tex envelope: {envelope}")

    palette = source[0x10:0x30]
    source_values = unpack_linear_4bpp(source[0x30:])
    values = list(source_values)
    x0, y0, x1, y1 = TEXT_BOX
    row_backgrounds = [source_values[y * WIDTH + 27] for y in range(y0, y1)]
    for y, background in zip(range(y0, y1), row_backgrounds, strict=True):
        for x in range(x0, x1):
            values[y * WIDTH + x] = background

    display_text = "뒤로"
    start_x = 6
    # The live GPU3D polygon reverses only the texture's vertical axis.
    # Store the complete phrase top-to-bottom flipped while preserving its
    # left-to-right reading order.
    phrase_mask = [[False] * 16 for _ in range(8)]
    for glyph_index, char in enumerate(display_text):
        glyph = load_source_glyph(args.font_png, char)
        for y, row in enumerate(glyph):
            for x, enabled in enumerate(row):
                phrase_mask[y][glyph_index * 8 + x] = enabled
    native_mask = Image.new("1", (16, 8))
    native_mask.putdata([enabled for row in phrase_mask for enabled in row])
    enlarged_mask = native_mask.resize((20, 10), Image.Resampling.NEAREST)
    enlarged_values = list(enlarged_mask.get_flattened_data())
    for source_y in range(10):
        for source_x in range(20):
            enabled = enlarged_values[source_y * 20 + source_x]
            if enabled:
                destination_x = start_x + source_x
                destination_y = y0 + (9 - source_y)
                values[destination_y * WIDTH + destination_x] = INK_INDEX

    changed = [
        index
        for index, (before, after) in enumerate(zip(source_values, values, strict=True))
        if before != after
    ]
    if any(not (x0 <= index % WIDTH < x1 and y0 <= index // WIDTH < y1) for index in changed):
        raise ValueError("preview changed pixels outside the text box")

    indexed_dir = args.output_root / "indexed"
    preview_dir = args.output_root / "preview_x8"
    indexed_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)
    source_png = indexed_dir / "myst00_tex_source.png"
    candidate_png = indexed_dir / "myst00_tex_back_ko.png"
    source_preview_png = preview_dir / "myst00_tex_source.png"
    candidate_preview_png = preview_dir / "myst00_tex_back_ko.png"
    runtime_preview_png = preview_dir / "myst00_tex_back_ko_runtime_expected.png"
    comparison_png = preview_dir / "myst00_tex_back_comparison.png"
    source_image = indexed_image(source_values, palette)
    candidate_image = indexed_image(values, palette)
    source_image.save(source_png)
    candidate_image.save(candidate_png)
    source_preview = checker_preview(source_image)
    candidate_preview = checker_preview(candidate_image)
    runtime_preview = checker_preview(
        candidate_image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    )
    source_preview.save(source_preview_png)
    candidate_preview.save(candidate_preview_png)
    runtime_preview.save(runtime_preview_png)
    comparison = Image.new("RGB", (source_preview.width * 2 + 16, source_preview.height), "white")
    comparison.paste(source_preview, (0, 0))
    comparison.paste(candidate_preview, (source_preview.width + 16, 0))
    comparison.save(comparison_png)

    report = {
        "schema_version": 1,
        "status": "png_preview_only_pending_user_approval",
        "resource_id": "sys_bg/myst00_tex",
        "runtime_evidence": {
            "pid": 22496,
            "renderer": "GPU3D",
            "polygon_index": 0,
            "screen_bounds": [112, 0, 144, 32],
            "texture_bank": "A",
            "texture_offset": "0x00600",
            "texture_format": "16-color linear 4bpp",
            "decoded_exact_match_offset": "0x30",
        },
        "source_resource": str(args.source_resource),
        "source_sha256": sha256(source),
        "translation": {"source": "もどる", "korean": display_text},
        "edit": {
            "text_box": list(TEXT_BOX),
            "text_origin": [start_x, y0],
            "native_phrase_size": [16, 8],
            "stored_phrase_size": [20, 10],
            "enlargement": "nearest_neighbor",
            "centered": True,
            "storage_transform": "flip_top_bottom",
            "transform_scope": "complete_two_glyph_phrase",
            "runtime_texture_transform": "flip_top_bottom",
            "runtime_expected_orientation": "upright_after_gpu3d_vertical_flip",
            "row_background_indices": row_backgrounds,
            "ink_index": INK_INDEX,
            "changed_pixel_count": len(changed),
            "changed_outside_text_box": 0,
            "non_text_pixels_preserved": True,
        },
        "font": {"path": str(args.font_png), "sha256": sha256(args.font_png.read_bytes())},
        "outputs": {
            "indexed_candidate": str(candidate_png.relative_to(args.output_root)),
            "indexed_candidate_sha256": sha256(candidate_png.read_bytes()),
            "comparison": str(comparison_png.relative_to(args.output_root)),
            "comparison_sha256": sha256(comparison_png.read_bytes()),
            "runtime_expected": str(runtime_preview_png.relative_to(args.output_root)),
            "runtime_expected_sha256": sha256(runtime_preview_png.read_bytes()),
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
