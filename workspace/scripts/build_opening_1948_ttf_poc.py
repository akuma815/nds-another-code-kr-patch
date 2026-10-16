#!/usr/bin/env python3
"""Build an isolated two-index 1948-card PoC from a fixed TrueType font."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from build_graphics_edit_canvases import indexed_image
from build_opening_1948_font_poc import fill_box, paste_mask
from render_pack_index_previews import render_index_preview


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def compose_ttf_text_mask(
    font: ImageFont.ImageFont,
    text: str,
    threshold: int,
) -> Image.Image:
    if not 1 <= threshold <= 254:
        raise ValueError("binary threshold must be between 1 and 254")
    probe = Image.new("L", (1, 1), 0)
    draw = ImageDraw.Draw(probe)
    bbox = draw.textbbox((0, 0), text, font=font)
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if width <= 0 or height <= 0:
        raise ValueError("TTF text rasterized empty")
    greyscale = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(greyscale)
    draw.text((-bbox[0], -bbox[1]), text, font=font, fill=255)
    mask = greyscale.point(lambda value: 255 if value >= threshold else 0, mode="1")
    active = mask.getbbox()
    if active is None:
        raise ValueError("TTF text became empty after thresholding")
    return mask.crop(active)


def build(args: argparse.Namespace) -> dict[str, object]:
    if args.output_root.exists() and any(args.output_root.iterdir()):
        raise FileExistsError(f"refusing nonempty output root: {args.output_root}")
    config_bytes = args.font_config.read_bytes()
    config = json.loads(config_bytes.decode("utf-8"))
    profile = config["font"]
    font_path = (args.font_config.parent / profile["path"]).resolve()
    font_bytes = font_path.read_bytes()
    if sha256(font_bytes) != profile["sha256"]:
        raise ValueError("opening TTF hash mismatch")

    with Image.open(args.reference) as reference:
        if reference.mode != "P" or reference.size != (256, 192):
            raise ValueError("1948 reference must be a 256x192 indexed PNG")
        reference_values = list(reference.get_flattened_data())
        reference_palette = reference.getpalette()
    width, height = 256, 192
    values = list(reference_values)
    background_index = 17
    ink_index = 255
    cleanup_box = [14, 49, 229, 74]
    fill_box(values, width, cleanup_box, background_index)

    font = ImageFont.truetype(str(font_path), int(profile["poc_size"]))
    threshold = int(profile["binary_threshold"])
    year_mask = compose_ttf_text_mask(font, "1948년", threshold)
    location_mask = compose_ttf_text_mask(font, "블러드 에드워드섬", threshold)
    year_box = paste_mask(values, width, height, year_mask, (129.0, 66.5), ink_index)
    location_box = paste_mask(
        values, width, height, location_mask, (128.5, 106.0), ink_index
    )

    changed = [index for index, pair in enumerate(zip(reference_values, values)) if pair[0] != pair[1]]
    if not changed:
        raise ValueError("TTF PoC produced no change")
    changed_values = {values[index] for index in changed}
    if not changed_values <= {background_index, ink_index}:
        raise ValueError("TTF PoC used an undeclared palette index")

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
        "status": "opening_1948_haeong_ttf_conditional_poc_prepared_static_only",
        "reference": {
            "path": str(args.reference),
            "sha256": sha256(args.reference.read_bytes()),
        },
        "font_config": {
            "path": str(args.font_config),
            "sha256": sha256(config_bytes),
        },
        "font": {"path": str(font_path), "sha256": sha256(font_bytes), **profile},
        "palette_contract": {
            "background_index": background_index,
            "ink_index": ink_index,
            "changed_output_indices": sorted(changed_values),
        },
        "layout": {
            "cleanup_box": cleanup_box,
            "year_box": year_box,
            "location_box": location_box,
        },
        "output": {
            "indexed_png": str(indexed_path),
            "indexed_png_sha256": sha256(indexed_path.read_bytes()),
            "preview_png": str(preview_path),
            "preview_png_sha256": sha256(preview_path.read_bytes()),
            "changed_pixel_count": len(changed),
        },
        "pass_criteria": [
            "1948년 and 블러드 에드워드섬 appear in the selected TTF with intact strokes",
            "no Japanese fragments remain",
            "the card background and transition remain intact",
        ],
        "not_proved": [
            "runtime visibility in melonDS",
            "other opening assets",
            "whole-game behavior",
        ],
    }
    report_path = args.output_root / "report.json"
    report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--font-config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
