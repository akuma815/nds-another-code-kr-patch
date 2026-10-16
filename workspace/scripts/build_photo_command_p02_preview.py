#!/usr/bin/env python3
"""Build a PNG-only Galmuri7 preview for pda/p02's photo command label."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct

from PIL import Image

from build_font_poc import load_source_glyph


SOURCE_SHA256 = "8C5DFEA40C452EBF24F2E8FA04B3BAB888EBF57366A935859C8B4D08E76C5B14"
TEXT_BOX = (14, 6, 65, 14)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def unpack_4bpp(data: bytes) -> list[int]:
    values = []
    for byte in data:
        values.extend((byte & 0x0F, byte >> 4))
    return values


def pack_4bpp(values: list[int]) -> bytes:
    if len(values) % 2:
        raise ValueError("4bpp values must have even length")
    return bytes(values[i] | (values[i + 1] << 4) for i in range(0, len(values), 2))


def glyph_mask(font_png: Path, text: str) -> Image.Image:
    result = Image.new("1", (8 * len(text), 8), 0)
    for glyph_index, char in enumerate(text):
        if char == " ":
            continue
        glyph = load_source_glyph(font_png, char)
        for y, row in enumerate(glyph):
            for x, value in enumerate(row):
                if value:
                    result.putpixel((glyph_index * 8 + x, y), 1)
    return result


def bgr555_palette(data: bytes) -> list[int]:
    result = []
    for offset in range(0, len(data), 2):
        value = struct.unpack_from("<H", data, offset)[0]
        result.extend(
            (
                (value & 0x1F) * 255 // 31,
                ((value >> 5) & 0x1F) * 255 // 31,
                ((value >> 10) & 0x1F) * 255 // 31,
            )
        )
    result.extend([0, 0, 0] * (256 - len(data) // 2))
    return result


def indexed_image(values: list[int], palette: bytes, size: tuple[int, int]) -> Image.Image:
    image = Image.new("P", size)
    image.putdata(values)
    image.putpalette(bgr555_palette(palette))
    image.info["transparency"] = 0
    return image


def checker(image: Image.Image, scale: int = 4) -> Image.Image:
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
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    if args.output_root.exists() and any(args.output_root.iterdir()):
        raise FileExistsError(f"refusing nonempty output root: {args.output_root}")
    indexed_dir = args.output_root / "indexed"
    preview_dir = args.output_root / "preview_x4"

    source = args.source_resource.read_bytes()
    if sha256(source) != SOURCE_SHA256:
        raise ValueError("pda/p02 source hash mismatch")
    flags, width, height, pixel_size, palette_size = struct.unpack_from(
        "<IHHII", source, 0
    )
    if (flags, width, height, pixel_size, palette_size) != (
        0x00100000,
        128,
        32,
        0x800,
        0x20,
    ):
        raise ValueError("unexpected pda/p02 envelope")
    palette = source[0x10:0x30]
    source_values = unpack_4bpp(source[0x30:])
    values = list(source_values)

    x0, y0, x1, y1 = TEXT_BOX
    erased = []
    for y in range(y0, y1):
        for x in range(x0, x1):
            position = y * width + x
            if source_values[position] == 15:
                values[position] = 0
                erased.append(position)
    if len(erased) != 142:
        raise ValueError(f"unexpected Japanese ink count in target box: {len(erased)}")

    display_text = "위에 표시"
    mask = glyph_mask(args.font_png, display_text)
    left = 20
    top = 6
    if not (x0 <= left and top >= y0 and left + mask.width <= x1 and top + mask.height <= y1):
        raise ValueError("Korean label does not fit registered text box")
    for y in range(mask.height):
        for x in range(mask.width):
            if mask.getpixel((x, y)):
                values[(top + y) * width + left + x] = 15

    changed = [
        index
        for index, (before, after) in enumerate(zip(source_values, values, strict=True))
        if before != after
    ]
    escaped = [
        index
        for index in changed
        if not (x0 <= index % width < x1 and y0 <= index // width < y1)
    ]
    if escaped:
        raise ValueError(f"preview changed protected pixels: {escaped[:8]}")
    if set(values) - set(source_values):
        raise ValueError("preview introduced a foreign palette index")

    source_image = indexed_image(source_values, palette, (width, height))
    output_image = indexed_image(values, palette, (width, height))
    indexed_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)
    source_png = indexed_dir / "pda_p02_source.png"
    output_png = indexed_dir / "pda_p02_up_display_ko.png"
    preview_png = preview_dir / "pda_p02_up_display_ko.png"
    compare_png = preview_dir / "pda_p02_up_display_comparison.png"
    source_image.save(source_png)
    output_image.save(output_png)
    output_preview = checker(output_image)
    output_preview.save(preview_png)
    source_preview = checker(source_image)
    comparison = Image.new("RGB", (source_preview.width, source_preview.height * 2 + 16), "white")
    comparison.paste(source_preview, (0, 0))
    comparison.paste(output_preview, (0, source_preview.height + 16))
    comparison.save(compare_png)

    report = {
        "schema_version": 1,
        "status": "png_preview_only_pending_user_approval",
        "resource_id": "pda/p02",
        "source_resource": str(args.source_resource),
        "source_sha256": sha256(source),
        "translation": {"source": "上に表示", "korean": display_text},
        "font": {"path": str(args.font_png), "sha256": sha256(args.font_png.read_bytes())},
        "edit": {
            "text_box": list(TEXT_BOX),
            "japanese_ink_index": 15,
            "replacement_ink_index": 15,
            "japanese_ink_pixels_erased": len(erased),
            "changed_pixels": len(changed),
            "changed_outside_text_box": 0,
            "foreign_palette_indices": [],
            "protected_frame_and_icons_preserved": True,
        },
        "outputs": {
            "indexed_source": str(source_png.relative_to(args.output_root)),
            "indexed_candidate": str(output_png.relative_to(args.output_root)),
            "preview_x4": str(preview_png.relative_to(args.output_root)),
            "comparison_x4": str(compare_png.relative_to(args.output_root)),
            "indexed_candidate_sha256": sha256(output_png.read_bytes()),
            "preview_x4_sha256": sha256(preview_png.read_bytes()),
            "comparison_x4_sha256": sha256(compare_png.read_bytes()),
        },
        "safety": {"decoded_binary_written": False, "rom_built": False},
    }
    report_path = args.output_root / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
