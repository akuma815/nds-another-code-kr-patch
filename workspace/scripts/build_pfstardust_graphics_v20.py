#!/usr/bin/env python3
"""Rebuild PF Stardust graphics with pixel-exact outline alignment.

The v19 renderer centered each mask with Python's round().  Banker's rounding
can place an odd-sized glyph mask and its two-pixel-larger dilated outline on
different pixel origins.  This wrapper preserves the v19 build logic while
using floor-based origins for every mask, so a radius-one outline begins
exactly one pixel above and to the left of its glyph mask.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PIL import Image, ImageFont

import build_pfstardust_graphics as v19
from build_opening_1948_font_poc import fill_box
from build_opening_1948_ttf_poc import compose_ttf_text_mask


def mask_origin_floor(
    mask: Image.Image, center: tuple[float, float]
) -> tuple[int, int]:
    # Outlined opening/chapter glyphs are at least 17 pixels high.  Preserve
    # v19 placement for compact menu and system labels that have no outline
    # and whose registered write boxes were tuned to round()-based placement.
    if mask.height < 17:
        return (
            round(center[0] - mask.width / 2),
            round(center[1] - mask.height / 2),
        )
    return (
        math.floor(center[0] - mask.width / 2),
        math.floor(center[1] - mask.height / 2),
    )


def paste_mask_floor(
    values: list[int],
    width: int,
    height: int,
    mask: Image.Image,
    center: tuple[float, float],
    ink_index: int,
) -> list[int]:
    left, top = mask_origin_floor(mask, center)
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


def copy_box(
    values: list[int], reference_values: list[int], width: int, box: list[int]
) -> None:
    x, y, box_width, box_height = box
    for row in range(y, y + box_height):
        start = row * width + x
        values[start : start + box_width] = reference_values[
            start : start + box_width
        ]


def rebuild_logo(
    canvas_id: str,
    reference_path: Path,
    output_root: Path,
    bold_font: ImageFont.FreeTypeFont,
    regular_font: ImageFont.FreeTypeFont,
    threshold: int,
) -> dict[str, object]:
    with Image.open(reference_path) as reference:
        width, height = reference.size
        reference_values = list(reference.get_flattened_data())
        palette = reference.getpalette()
    values = list(reference_values)
    cleanup_box = [8, 76, 240, 50]
    fill_box(values, width, cleanup_box, 255)
    copy_box(values, reference_values, width, [228, 104, 20, 12])

    # Recreate the source logo's motion-line hierarchy behind the title.
    # The long middle stroke is the missing visual anchor; shorter strokes at
    # neighboring levels retain the fast horizontal sweep without replacing
    # the Korean glyphs.
    speed_lines = [
        ([20, 82, 38, 1], 244), ([198, 82, 38, 1], 244),
        ([16, 91, 58, 1], 242), ([182, 91, 58, 1], 242),
        ([10, 101, 236, 1], 242),
        ([20, 104, 45, 1], 244), ([191, 104, 45, 1], 244),
        ([38, 107, 38, 1], 246), ([180, 107, 38, 1], 246),
    ]
    operations: list[dict[str, object]] = [
        {"kind": "fill_box", "box": cleanup_box, "background_index": 255},
        {"kind": "preserve_box", "box": [228, 104, 20, 12]},
    ]
    for box, index in speed_lines:
        fill_box(values, width, box, index)
        operations.append({"kind": "source_style_speed_line", "box": box, "index": index})

    main_mask = compose_ttf_text_mask(bold_font, "어나더 코드", threshold)
    main_clearance_mask = v19.dilate_binary_mask(main_mask, 1)
    clearance_box = paste_mask_floor(
        values, width, height, main_clearance_mask, (128.0, 94.0), 255
    )
    operations.append(
        {
            "kind": "speed_line_glyph_clearance",
            "radius": 1,
            "box": clearance_box,
            "background_index": 255,
        }
    )
    main_box = paste_mask_floor(values, width, height, main_mask, (128.0, 94.0), 241)
    operations.append(
        {
            "kind": "binary_ttf_text",
            "text": "어나더 코드",
            "font_role": "bold",
            "font_size": bold_font.size,
            "center": [128.0, 94.0],
            "output_box": main_box,
            "ink_index": 241,
        }
    )
    subtitle_mask = compose_ttf_text_mask(regular_font, "두 개의 기억", threshold)
    subtitle_box = paste_mask_floor(
        values, width, height, subtitle_mask, (128.0, 117.0), 244
    )
    operations.append(
        {
            "kind": "binary_ttf_text",
            "text": "두 개의 기억",
            "font_role": "regular",
            "font_size": regular_font.size,
            "center": [128.0, 117.0],
            "output_box": subtitle_box,
            "ink_index": 244,
        }
    )

    indexed_path = output_root / "indexed" / f"{canvas_id}.png"
    with v19.indexed_image(values, width, height) as output:
        if palette is not None:
            output.putpalette(palette)
        output.save(indexed_path, format="PNG", optimize=False)
    preview_path = output_root / "preview_x4" / f"{canvas_id}.png"
    with v19.render_index_preview(values, width, height) as preview:
        preview.resize((width * 4, height * 4), Image.Resampling.NEAREST).save(
            preview_path, format="PNG", optimize=False
        )
    changed = [
        position
        for position, pair in enumerate(zip(reference_values, values))
        if pair[0] != pair[1]
    ]
    return {
        "canvas_id": canvas_id,
        "action": "pf_stardust_logo_rebuilt_with_source_style_speed_lines_and_regular_subtitle",
        "changed_pixel_count": len(changed),
        "change_boxes": [cleanup_box],
        "operations": operations,
        "indexed_png": str(indexed_path.relative_to(output_root)),
        "indexed_png_sha256": v19.sha256(indexed_path.read_bytes()),
        "preview_png": str(preview_path.relative_to(output_root)),
        "preview_png_sha256": v19.sha256(preview_path.read_bytes()),
    }


def build(args: argparse.Namespace) -> dict[str, object]:
    original_paste_mask = v19.paste_mask
    v19.paste_mask = paste_mask_floor
    try:
        result = v19.build(args)
    finally:
        v19.paste_mask = original_paste_mask

    logo_font_config_bytes = args.logo_font_config.read_bytes()
    logo_font_config = json.loads(logo_font_config_bytes.decode("utf-8"))
    loaded_fonts: dict[str, ImageFont.FreeTypeFont] = {}
    font_report: dict[str, object] = {}
    for role in ("bold", "regular"):
        profile = logo_font_config[role]
        font_path = (args.logo_font_config.parent / profile["path"]).resolve()
        font_bytes = font_path.read_bytes()
        actual_hash = v19.sha256(font_bytes)
        if actual_hash != profile["sha256"]:
            raise ValueError(f"logo {role} font hash mismatch")
        loaded_fonts[role] = ImageFont.truetype(str(font_path), int(profile["size"]))
        font_report[role] = {
            "path": str(font_path),
            "sha256": actual_hash,
            "size": int(profile["size"]),
        }

    logo_ids = {"title_logo", "opening_title_logo_a", "opening_title_logo_b"}
    rebuilt_logos = {
        canvas_id: rebuild_logo(
            canvas_id,
            args.reference_dir / f"{canvas_id}.png",
            args.output_root,
            loaded_fonts["bold"],
            loaded_fonts["regular"],
            128,
        )
        for canvas_id in logo_ids
    }
    result["outputs"] = [
        rebuilt_logos.get(row["canvas_id"], row) for row in result["outputs"]
    ]
    v19.write_contact_sheet(args.output_root, result["outputs"])

    result["status"] = "full_graphics_set_rebuilt_with_pf_stardust_pixel_aligned_outlines_static_only"
    result["placement_policy"] = {
        "name": "floor_center_origin",
        "reason": "keep radius-one outlines exactly one pixel around glyph masks",
    }
    result["logo_font_config"] = {
        "path": str(args.logo_font_config),
        "sha256": v19.sha256(logo_font_config_bytes),
    }
    result["logo_fonts"] = font_report
    result["logo_policy"] = {
        "speed_lines": "source-style long central line and graduated side streaks",
        "subtitle": "PF Stardust 3.0 regular",
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
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
