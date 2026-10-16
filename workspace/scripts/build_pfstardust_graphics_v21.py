#!/usr/bin/env python3
"""Build v21 graphics with stroke-safe PF Stardust Regular small text."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageFont

import build_pfstardust_graphics as v19
import build_pfstardust_graphics_v20 as v20
from build_opening_1948_font_poc import fill_box
from build_opening_1948_ttf_poc import compose_ttf_text_mask


def threshold_binary(greyscale: Image.Image, threshold: int) -> Image.Image:
    if not 1 <= threshold <= 254:
        raise ValueError("binary threshold must be between 1 and 254")
    return greyscale.point(
        lambda value: 255 if value >= threshold else 0, mode="1"
    )


def save_canvas(
    output_root: Path,
    canvas_id: str,
    values: list[int],
    width: int,
    height: int,
    palette: list[int] | None,
) -> tuple[Path, Path]:
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
    return indexed_path, preview_path


def common_row(
    canvas_id: str,
    action: str,
    operations: list[dict[str, object]],
    change_boxes: list[list[int]],
    reference_values: list[int],
    values: list[int],
    output_root: Path,
    indexed_path: Path,
    preview_path: Path,
) -> dict[str, object]:
    changed = [
        position
        for position, pair in enumerate(zip(reference_values, values))
        if pair[0] != pair[1]
    ]
    return {
        "canvas_id": canvas_id,
        "action": action,
        "changed_pixel_count": len(changed),
        "change_boxes": change_boxes,
        "operations": operations,
        "indexed_png": str(indexed_path.relative_to(output_root)),
        "indexed_png_sha256": v19.sha256(indexed_path.read_bytes()),
        "preview_png": str(preview_path.relative_to(output_root)),
        "preview_png_sha256": v19.sha256(preview_path.read_bytes()),
    }


def rebuild_subtitle(
    canvas_id: str,
    reference_path: Path,
    output_root: Path,
    regular_font: ImageFont.FreeTypeFont,
    threshold: int,
    previous_row: dict[str, object],
) -> dict[str, object]:
    indexed_path = output_root / "indexed" / f"{canvas_id}.png"
    with Image.open(reference_path) as reference:
        width, height = reference.size
        reference_values = list(reference.get_flattened_data())
    with Image.open(indexed_path) as current:
        values = list(current.get_flattened_data())
        palette = current.getpalette()
    subtitle_cleanup = [90, 110, 76, 14]
    fill_box(values, width, subtitle_cleanup, 255)
    mask = compose_ttf_text_mask(regular_font, "두 개의 기억", threshold)
    output_box = v20.paste_mask_floor(
        values, width, height, mask, (128.0, 117.0), 244
    )
    indexed_path, preview_path = save_canvas(
        output_root, canvas_id, values, width, height, palette
    )
    operations = [
        operation
        for operation in previous_row["operations"]
        if not (
            operation.get("kind") == "binary_ttf_text"
            and operation.get("text") == "두 개의 기억"
        )
    ]
    operations.extend(
        [
            {
                "kind": "small_text_cleanup",
                "box": subtitle_cleanup,
                "background_index": 255,
            },
            {
                "kind": "binary_ttf_text",
                "text": "두 개의 기억",
                "font_role": "regular_stroke_safe",
                "font_size": regular_font.size,
                "binary_threshold": threshold,
                "center": [128.0, 117.0],
                "output_box": output_box,
                "ink_index": 244,
            },
        ]
    )
    return common_row(
        canvas_id,
        "pf_stardust_logo_with_stroke_safe_regular_subtitle",
        operations,
        [list(box) for box in previous_row["change_boxes"]],
        reference_values,
        values,
        output_root,
        indexed_path,
        preview_path,
    )


def rebuild_small_label(
    canvas_id: str,
    reference_path: Path,
    output_root: Path,
    spec: dict[str, object],
    regular_font_path: Path,
    threshold: int,
) -> dict[str, object]:
    with Image.open(reference_path) as reference:
        width, height = reference.size
        reference_values = list(reference.get_flattened_data())
        palette = reference.getpalette()
    values = list(reference_values)
    operations: list[dict[str, object]] = []
    declared_indices: set[int] = set()
    for restore in spec.get("restore_rows", []):
        box = list(restore["box"])
        sample_box = list(restore["sample_box"])
        row_indices = v19.restore_rows_from_reference(
            values,
            reference_values,
            width,
            box,
            sample_box,
            str(restore.get("method", "max")),
        )
        declared_indices.update(row_indices)
        operations.append(
            {
                "kind": "restore_rows",
                "box": box,
                "sample_box": sample_box,
                "method": restore.get("method", "max"),
                "row_indices": row_indices,
            }
        )
    for label in spec["labels"]:
        font = ImageFont.truetype(str(regular_font_path), int(label["size"]))
        mask = compose_ttf_text_mask(font, str(label["text"]), threshold)
        shadow = label.get("shadow")
        if shadow:
            shadow_index = int(shadow["index"])
            offset_x, offset_y = shadow["offset"]
            shadow_box = v19.paste_mask(
                values,
                width,
                height,
                mask,
                (
                    float(label["center"][0]) + int(offset_x),
                    float(label["center"][1]) + int(offset_y),
                ),
                shadow_index,
            )
            declared_indices.add(shadow_index)
            operations.append(
                {
                    "kind": "shadow",
                    "text": label["text"],
                    "box": shadow_box,
                    "index": shadow_index,
                }
            )
        ink_index = int(label["ink_index"])
        output_box = v19.paste_mask(
            values,
            width,
            height,
            mask,
            tuple(label["center"]),
            ink_index,
        )
        if not v19.inside(output_box[:4], list(label["bounds_box"])):
            raise ValueError(f"regular label exceeds bounds: {canvas_id}")
        declared_indices.add(ink_index)
        operations.append(
            {
                "kind": "binary_ttf_text",
                "text": label["text"],
                "font_role": "regular_stroke_safe",
                "font_size": label["size"],
                "binary_threshold": threshold,
                "center": label["center"],
                "output_box": output_box,
                "ink_index": ink_index,
            }
        )
    source_indices = set(reference_values)
    if not declared_indices <= source_indices or not set(values) <= source_indices:
        raise ValueError(f"small label uses foreign palette indices: {canvas_id}")
    change_boxes = [list(box) for box in spec["change_boxes"]]
    changed = [
        position
        for position, pair in enumerate(zip(reference_values, values))
        if pair[0] != pair[1]
    ]
    if not v19.changed_inside_boxes(changed, width, change_boxes):
        raise ValueError(f"small label escaped registered boxes: {canvas_id}")
    indexed_path, preview_path = save_canvas(
        output_root, canvas_id, values, width, height, palette
    )
    return common_row(
        canvas_id,
        "pf_stardust_regular_stroke_safe_small_label",
        operations,
        change_boxes,
        reference_values,
        values,
        output_root,
        indexed_path,
        preview_path,
    )


def build(args: argparse.Namespace) -> dict[str, object]:
    result = v20.build(args)
    small_config_bytes = args.small_text_config.read_bytes()
    small_config = json.loads(small_config_bytes.decode("utf-8"))
    profile = small_config["font"]
    regular_font_path = (args.small_text_config.parent / profile["path"]).resolve()
    regular_font_bytes = regular_font_path.read_bytes()
    if v19.sha256(regular_font_bytes) != profile["sha256"]:
        raise ValueError("small-text regular font hash mismatch")
    threshold = int(profile["binary_threshold"])
    regular_subtitle_font = ImageFont.truetype(str(regular_font_path), 12)
    graphics_config = json.loads(args.graphics_config.read_text(encoding="utf-8"))
    targets = graphics_config["canvases"]
    previous_rows = {row["canvas_id"]: row for row in result["outputs"]}
    rebuilt: dict[str, dict[str, object]] = {}
    for canvas_id in small_config["targets"]["title_subtitle"]:
        rebuilt[canvas_id] = rebuild_subtitle(
            canvas_id,
            args.reference_dir / f"{canvas_id}.png",
            args.output_root,
            regular_subtitle_font,
            threshold,
            previous_rows[canvas_id],
        )
    for canvas_id in small_config["targets"]["pda_and_system_labels"]:
        rebuilt[canvas_id] = rebuild_small_label(
            canvas_id,
            args.reference_dir / f"{canvas_id}.png",
            args.output_root,
            targets[canvas_id],
            regular_font_path,
            threshold,
        )
    result["outputs"] = [
        rebuilt.get(row["canvas_id"], row) for row in result["outputs"]
    ]
    v19.write_contact_sheet(args.output_root, result["outputs"])
    result["status"] = "pf_stardust_regular_small_text_stroke_safe_static_only"
    result["small_text_profile"] = {
        "config_path": str(args.small_text_config),
        "config_sha256": v19.sha256(small_config_bytes),
        "font_path": str(regular_font_path),
        "font_sha256": v19.sha256(regular_font_bytes),
        "binary_threshold": threshold,
        "reason": "preserve thin horizontal strokes at 9-12px",
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
