#!/usr/bin/env python3
"""Build a PNG-only preview for the GPU3D sys_bg/myst03_tex restart button."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct

from PIL import Image

from build_font_poc import load_source_glyph


SOURCE_SHA256 = "123A0B6FF78FD03E522FDC5E8112F61EEF7ACECD9526C83BF8440B75E4842BD8"
WIDTH = 32
HEIGHT = 32
RUNTIME_TEXT_BOX = (2, 9, 30, 19)
RUNTIME_ROW_BACKGROUNDS = (11, 4, 10, 10, 11, 9, 9, 7, 7, 7)
INK_INDEX = 14


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def unpack_linear_4bpp(data: bytes) -> list[int]:
    if len(data) != WIDTH * HEIGHT // 2:
        raise ValueError("unexpected sys_bg/myst03_tex texture size")
    values: list[int] = []
    for value in data:
        values.extend((value & 0x0F, value >> 4))
    return values


def flip_top_bottom(values: list[int]) -> list[int]:
    return [
        values[(HEIGHT - 1 - y) * WIDTH + x]
        for y in range(HEIGHT)
        for x in range(WIDTH)
    ]


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
        raise ValueError("sys_bg/myst03_tex source hash mismatch")
    envelope = struct.unpack_from("<IHHII", source, 0)
    if envelope != (0x00100000, WIDTH, HEIGHT, 0x200, 0x20):
        raise ValueError(f"unexpected sys_bg/myst03_tex envelope: {envelope}")

    palette = source[0x10:0x30]
    source_stored = unpack_linear_4bpp(source[0x30:])
    source_runtime = flip_top_bottom(source_stored)
    runtime_values = list(source_runtime)
    x0, y0, x1, y1 = RUNTIME_TEXT_BOX
    for y, background in zip(range(y0, y1), RUNTIME_ROW_BACKGROUNDS, strict=True):
        for x in range(x0, x1):
            runtime_values[y * WIDTH + x] = background

    display_text = "처음부터"
    start_x = 3
    start_y = 11
    glyph_offsets = (0, 6, 13, 20)
    for glyph_index, char in enumerate(display_text):
        glyph = load_source_glyph(args.font_png, char)
        for glyph_y, row in enumerate(glyph[:7]):
            for glyph_x, enabled in enumerate(row[:7]):
                if enabled:
                    destination_x = start_x + glyph_offsets[glyph_index] + glyph_x
                    destination_y = start_y + glyph_y
                    if not (x0 <= destination_x < x1 and y0 <= destination_y < y1):
                        raise ValueError("Korean phrase escaped the registered text box")
                    runtime_values[destination_y * WIDTH + destination_x] = INK_INDEX

    stored_values = flip_top_bottom(runtime_values)
    changed = [
        index
        for index, (before, after) in enumerate(
            zip(source_stored, stored_values, strict=True)
        )
        if before != after
    ]
    stored_y0 = HEIGHT - y1
    stored_y1 = HEIGHT - y0
    if any(
        not (x0 <= index % WIDTH < x1 and stored_y0 <= index // WIDTH < stored_y1)
        for index in changed
    ):
        raise ValueError("preview changed pixels outside the registered text box")

    indexed_dir = args.output_root / "indexed"
    preview_dir = args.output_root / "preview_x8"
    indexed_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)
    source_image = indexed_image(source_stored, palette)
    candidate_image = indexed_image(stored_values, palette)
    source_runtime_image = indexed_image(source_runtime, palette)
    candidate_runtime_image = indexed_image(runtime_values, palette)
    source_png = indexed_dir / "myst03_tex_source.png"
    candidate_png = indexed_dir / "myst03_tex_restart_ko.png"
    source_runtime_png = preview_dir / "myst03_tex_source_runtime.png"
    candidate_runtime_png = preview_dir / "myst03_tex_restart_ko_runtime_expected.png"
    comparison_png = preview_dir / "myst03_tex_runtime_comparison.png"
    source_image.save(source_png)
    candidate_image.save(candidate_png)
    source_preview = checker_preview(source_runtime_image)
    candidate_preview = checker_preview(candidate_runtime_image)
    source_preview.save(source_runtime_png)
    candidate_preview.save(candidate_runtime_png)
    comparison = Image.new("RGB", (source_preview.width * 2 + 16, source_preview.height), "white")
    comparison.paste(source_preview, (0, 0))
    comparison.paste(candidate_preview, (source_preview.width + 16, 0))
    comparison.save(comparison_png)

    report = {
        "schema_version": 1,
        "status": "png_preview_only_pending_user_approval",
        "resource_id": "sys_bg/myst03_tex",
        "runtime_evidence": {
            "pid": 1188,
            "renderer": "GPU3D",
            "polygon_index": 1,
            "screen_bounds": [0, 160, 32, 192],
            "texture_bank": "A",
            "texture_offset": "0x00200",
            "texture_format": "16-color linear 4bpp",
            "texture_palette_bank": "E",
            "texture_palette_offset": "0x00020",
            "decoded_exact_match_offset": "0x30",
        },
        "source_resource": str(args.source_resource),
        "source_sha256": sha256(source),
        "translation": {"source": "最初から", "korean": display_text},
        "edit": {
            "runtime_text_box": list(RUNTIME_TEXT_BOX),
            "stored_text_box": [x0, stored_y0, x1, stored_y1],
            "runtime_text_origin": [start_x, start_y],
            "glyph_offsets": list(glyph_offsets),
            "phrase_ink_width": 26,
            "storage_transform": "flip_top_bottom",
            "runtime_texture_transform": "flip_top_bottom",
            "runtime_row_background_indices": list(RUNTIME_ROW_BACKGROUNDS),
            "ink_index": INK_INDEX,
            "changed_pixel_count": len(changed),
            "changed_outside_text_box": 0,
            "non_text_pixels_preserved": True,
        },
        "font": {"path": str(args.font_png), "sha256": sha256(args.font_png.read_bytes())},
        "outputs": {
            "indexed_candidate": str(candidate_png.relative_to(args.output_root)),
            "indexed_candidate_sha256": sha256(candidate_png.read_bytes()),
            "runtime_comparison": str(comparison_png.relative_to(args.output_root)),
            "runtime_comparison_sha256": sha256(comparison_png.read_bytes()),
            "runtime_expected": str(candidate_runtime_png.relative_to(args.output_root)),
            "runtime_expected_sha256": sha256(candidate_runtime_png.read_bytes()),
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
