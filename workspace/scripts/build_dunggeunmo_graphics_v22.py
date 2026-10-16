#!/usr/bin/env python3
"""Build image-only DungGeunMo previews for title subtitle and PDA labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import build_pfstardust_graphics as v19
import build_pfstardust_graphics_v21 as v21


def compose_mono_hinted_text_mask(
    font: ImageFont.ImageFont,
    text: str,
    _compatibility_threshold: int,
) -> Image.Image:
    probe = Image.new("1", (1, 1), 0)
    draw = ImageDraw.Draw(probe)
    draw.fontmode = "1"
    bbox = draw.textbbox((0, 0), text, font=font)
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if width <= 0 or height <= 0:
        raise ValueError("mono-hinted text rasterized empty")
    mask = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    draw.fontmode = "1"
    draw.text((-bbox[0], -bbox[1]), text, font=font, fill=1)
    active = mask.getbbox()
    if active is None:
        raise ValueError("mono-hinted text became empty")
    return mask.crop(active)


def build(args: argparse.Namespace) -> dict[str, object]:
    original_compose = v21.compose_ttf_text_mask
    v21.compose_ttf_text_mask = compose_mono_hinted_text_mask
    try:
        result = v21.build(args)
    finally:
        v21.compose_ttf_text_mask = original_compose

    config_bytes = args.small_text_config.read_bytes()
    config = json.loads(config_bytes.decode("utf-8"))
    title_ids = set(config["targets"]["title_subtitle"])
    label_ids = set(config["targets"]["pda_and_system_labels"])
    target_ids = title_ids | label_ids
    for row in result["outputs"]:
        if row["canvas_id"] not in target_ids:
            continue
        row["action"] = (
            "pf_stardust_bold_logo_with_dunggeunmo_mono_hinted_subtitle"
            if row["canvas_id"] in title_ids
            else "dunggeunmo_mono_hinted_small_label"
        )
        for operation in row["operations"]:
            if operation.get("font_role") != "regular_stroke_safe":
                continue
            operation["font_role"] = "dunggeunmo_mono_hinted"
            operation.pop("binary_threshold", None)
            operation["render_mode"] = "freetype_mono_hinting"

    profile = config["font"]
    font_path = (args.small_text_config.parent / profile["path"]).resolve()
    result["status"] = "dunggeunmo_small_text_image_preview_static_only"
    result["logo_policy"]["subtitle"] = (
        "DungGeunMo 12px rendered with FreeType monochrome hinting"
    )
    result["small_text_profile"] = {
        "config_path": str(args.small_text_config),
        "config_sha256": v19.sha256(config_bytes),
        "font_path": str(font_path),
        "font_sha256": v19.sha256(font_path.read_bytes()),
        "render_mode": "freetype_mono_hinting",
        "targets": sorted(target_ids),
        "scope": "image_preview_only_no_rom_build",
    }
    report_path = args.output_root / "report.json"
    report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--graphics-config", type=Path, required=True)
    parser.add_argument("--logo-font-config", type=Path, required=True)
    parser.add_argument("--small-text-config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
