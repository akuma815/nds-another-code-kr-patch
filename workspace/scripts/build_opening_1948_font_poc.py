#!/usr/bin/env python3
"""Build an isolated indexed 1948-card font PoC from a fixed Hangul atlas."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image

from build_graphics_edit_canvases import indexed_image
from render_pack_index_previews import render_index_preview


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def box_edges(box: list[int]) -> tuple[int, int, int, int]:
    x, y, width, height = box
    return x, y, x + width, y + height


def fill_box(values: list[int], width: int, box: list[int], value: int) -> None:
    x0, y0, x1, y1 = box_edges(box)
    for y in range(y0, y1):
        values[y * width + x0 : y * width + x1] = [value] * (x1 - x0)


def copy_box(
    target: list[int], source: list[int], width: int, box: list[int]
) -> None:
    x0, y0, x1, y1 = box_edges(box)
    for y in range(y0, y1):
        start = y * width + x0
        target[start : start + (x1 - x0)] = source[start : start + (x1 - x0)]


def glyph_mask(
    atlas: Image.Image,
    character: str,
    cell_width: int,
    cell_height: int,
    columns: int,
    hangul_start: int,
    hangul_end: int,
    ink_rgba: tuple[int, int, int, int],
) -> Image.Image:
    codepoint = ord(character)
    if not hangul_start <= codepoint <= hangul_end:
        raise ValueError(f"atlas has no declared glyph for U+{codepoint:04X}")
    index = codepoint - hangul_start
    x = index % columns * cell_width
    y = index // columns * cell_height
    cell = atlas.crop((x, y, x + cell_width, y + cell_height)).convert("RGBA")
    mask = Image.new("1", (cell_width, cell_height), 0)
    source = cell.load()
    target = mask.load()
    active: list[tuple[int, int]] = []
    for local_y in range(cell_height):
        for local_x in range(cell_width):
            if source[local_x, local_y] == ink_rgba:
                target[local_x, local_y] = 1
                active.append((local_x, local_y))
    if not active:
        raise ValueError(f"atlas glyph is empty for U+{codepoint:04X}")
    xs = [point[0] for point in active]
    ys = [point[1] for point in active]
    return mask.crop((min(xs), min(ys), max(xs) + 1, max(ys) + 1))


def compose_text_mask(
    atlas: Image.Image,
    text: str,
    cell_size: tuple[int, int],
    columns: int,
    hangul_start: int,
    hangul_end: int,
    ink_rgba: tuple[int, int, int, int],
    scale: int,
    space_width: int = 5,
) -> Image.Image:
    glyphs: list[Image.Image] = []
    for character in text:
        if character == " ":
            glyphs.append(Image.new("1", (space_width, 1), 0))
        else:
            glyphs.append(
                glyph_mask(
                    atlas,
                    character,
                    cell_size[0],
                    cell_size[1],
                    columns,
                    hangul_start,
                    hangul_end,
                    ink_rgba,
                )
            )
    width = sum(glyph.width for glyph in glyphs)
    height = max(glyph.height for glyph in glyphs)
    combined = Image.new("1", (width, height), 0)
    cursor = 0
    for glyph in glyphs:
        combined.paste(glyph, (cursor, 0))
        cursor += glyph.width
    return combined.resize(
        (combined.width * scale, combined.height * scale), Image.Resampling.NEAREST
    )


def paste_mask(
    values: list[int],
    width: int,
    height: int,
    mask: Image.Image,
    center: tuple[float, float],
    ink_index: int,
) -> list[int]:
    left = round(center[0] - mask.width / 2)
    top = round(center[1] - mask.height / 2)
    if left < 0 or top < 0 or left + mask.width > width or top + mask.height > height:
        raise ValueError("font mask exceeds target canvas")
    pixels = list(mask.get_flattened_data())
    written = 0
    for local_y in range(mask.height):
        for local_x in range(mask.width):
            if pixels[local_y * mask.width + local_x]:
                values[(top + local_y) * width + left + local_x] = ink_index
                written += 1
    return [left, top, mask.width, mask.height, written]


def build(args: argparse.Namespace) -> dict[str, object]:
    if args.output_root.exists() and any(args.output_root.iterdir()):
        raise FileExistsError(f"refusing nonempty output root: {args.output_root}")
    config_bytes = args.font_config.read_bytes()
    config = json.loads(config_bytes.decode("utf-8"))
    font = config["small_opening_font"]
    atlas_path = (args.font_config.parent / font["path"]).resolve()
    atlas_bytes = atlas_path.read_bytes()
    if sha256(atlas_bytes) != font["sha256"]:
        raise ValueError("small opening font hash mismatch")

    with Image.open(args.reference) as reference:
        if reference.mode != "P" or reference.size != (256, 192):
            raise ValueError("1948 reference must be a 256x192 indexed PNG")
        reference_values = list(reference.get_flattened_data())
        reference_palette = reference.getpalette()
    width, height = 256, 192
    values = list(reference_values)
    background_index = 17
    ink_index = 255

    # Remove the complete source-language card, including tile-edge fragments,
    # then restore only the original 1948 digits. This isolates the Hangul mask
    # path without asking the Hangul-only atlas to provide Arabic numerals.
    fill_box(values, width, [14, 49, 229, 74], background_index)
    copy_box(values, reference_values, width, [95, 57, 48, 19])

    with Image.open(atlas_path) as source_atlas:
        if list(source_atlas.size) != font["image_size"]:
            raise ValueError("small opening font dimensions mismatch")
        atlas = source_atlas.convert("RGBA")
        kwargs = {
            "cell_size": tuple(font["cell_size"]),
            "columns": int(font["columns"]),
            "hangul_start": int(font["hangul_start"]),
            "hangul_end": int(font["hangul_end"]),
            "ink_rgba": tuple(font["ink_rgba"]),
            "scale": int(font["poc_scale"]),
        }
        year_mask = compose_text_mask(atlas, "년", **kwargs)
        location_mask = compose_text_mask(atlas, "블러드 에드워드섬", **kwargs)

    year_box = paste_mask(values, width, height, year_mask, (153.5, 66.5), ink_index)
    location_box = paste_mask(
        values, width, height, location_mask, (130.5, 106.0), ink_index
    )
    changed = [index for index, pair in enumerate(zip(reference_values, values)) if pair[0] != pair[1]]
    if not changed:
        raise ValueError("font PoC produced no change")
    permitted = {background_index, ink_index}
    changed_values = {values[index] for index in changed}
    if not changed_values <= permitted:
        raise ValueError(f"PoC used undeclared indices: {sorted(changed_values - permitted)}")

    args.output_root.mkdir(parents=True, exist_ok=True)
    indexed_dir = args.output_root / "indexed"
    preview_dir = args.output_root / "preview_x4"
    indexed_dir.mkdir()
    preview_dir.mkdir()
    indexed_path = indexed_dir / "opening_1948.png"
    with indexed_image(values, width, height) as output:
        if reference_palette is not None:
            output.putpalette(reference_palette)
        output.save(indexed_path, format="PNG", optimize=False)
    preview_path = preview_dir / "opening_1948.png"
    with render_index_preview(values, width, height) as preview:
        preview.resize((1024, 768), Image.Resampling.NEAREST).save(
            preview_path, format="PNG", optimize=False
        )

    result = {
        "schema_version": 1,
        "status": "opening_1948_bold_font_conditional_poc_prepared_static_only",
        "reference": {
            "path": str(args.reference),
            "sha256": sha256(args.reference.read_bytes()),
        },
        "font_config": {
            "path": str(args.font_config),
            "sha256": sha256(config_bytes),
        },
        "font": {"path": str(atlas_path), "sha256": sha256(atlas_bytes), **font},
        "palette_contract": {
            "background_index": background_index,
            "ink_index": ink_index,
            "changed_output_indices": sorted(changed_values),
        },
        "layout": {
            "cleanup_box": [14, 49, 229, 74],
            "restored_digits_box": [95, 57, 48, 19],
            "year_box": year_box,
            "location_box": location_box
        },
        "output": {
            "indexed_png": str(indexed_path),
            "indexed_png_sha256": sha256(indexed_path.read_bytes()),
            "preview_png": str(preview_path),
            "preview_png_sha256": sha256(preview_path.read_bytes()),
            "changed_pixel_count": len(changed),
        },
        "pass_criteria": [
            "년 and 블러드 에드워드섬 appear with intact Galmuri11 Bold strokes",
            "the original 1948 digits remain intact",
            "the card background and transition remain intact",
        ],
        "not_proved": [
            "runtime visibility in melonDS",
            "Galmuri14Bitmap large-caption rendering",
            "other opening assets",
        ],
    }
    report_path = args.output_root / "report.json"
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--font-config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = build(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
