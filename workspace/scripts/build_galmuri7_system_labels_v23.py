#!/usr/bin/env python3
"""Replace the three PDA/system labels with native Galmuri7 8x8 cells.

The existing v22 image set remains the base.  Only assets that display the
labels 뒤로, 조사, and 대화 (including their state variants) are rebuilt from
the clean indexed edit canvases.  No ROM is written by this preview builder.
"""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path

from PIL import Image

import build_pfstardust_graphics as v19
import build_pfstardust_graphics_v21 as v21
from build_font_poc import load_source_glyph


def compose_bitmap_mask(font_png: Path, text: str) -> Image.Image:
    mask = Image.new("1", (8 * len(text), 8), 0)
    for index, char in enumerate(text):
        glyph = load_source_glyph(font_png, char)
        if len(glyph) != 8 or any(len(row) != 8 for row in glyph):
            raise ValueError(f"Galmuri7 glyph is not 8x8: {char}")
        for y, row in enumerate(glyph):
            for x, value in enumerate(row):
                if value:
                    mask.putpixel((index * 8 + x, y), 1)
    if mask.getbbox() is None:
        raise ValueError(f"Galmuri7 label rendered empty: {text}")
    return mask


def restore_selected_indices(
    values: list[int],
    reference_values: list[int],
    width: int,
    box: list[int],
    sample_box: list[int],
    method: str,
    only_indices: set[int],
    ignore_indices: set[int],
) -> tuple[list[int], set[int]]:
    """Erase only source text indices while preserving borders/transparency."""
    x0, y0, x1, y1 = v19.box_edges(box)
    sx0, sy0, sx1, sy1 = v19.box_edges(sample_box)
    if (y0, y1) != (sy0, sy1):
        raise ValueError("restore and sample boxes must cover identical rows")
    restored: list[int] = []
    changed_positions: set[int] = set()
    excluded = only_indices | ignore_indices
    for y in range(y0, y1):
        samples = [
            reference_values[y * width + x]
            for x in range(sx0, sx1)
            if reference_values[y * width + x] not in excluded
        ]
        if not samples:
            raise ValueError(f"no background samples remain on row {y}")
        if method == "mode":
            background = Counter(samples).most_common(1)[0][0]
        elif method == "max":
            background = max(samples)
        else:
            raise ValueError(f"unsupported selective restore method: {method}")
        for x in range(x0, x1):
            position = y * width + x
            if reference_values[position] in only_indices:
                values[position] = background
                changed_positions.add(position)
        restored.append(background)
    return restored, changed_positions


def mask_positions(
    mask: Image.Image, center: tuple[float, float], width: int
) -> set[int]:
    left = round(center[0] - mask.width / 2)
    top = round(center[1] - mask.height / 2)
    pixels = list(mask.get_flattened_data())
    return {
        (top + y) * width + left + x
        for y in range(mask.height)
        for x in range(mask.width)
        if pixels[y * mask.width + x]
    }


def rebuild_label(
    canvas_id: str,
    reference_path: Path,
    output_root: Path,
    spec: dict[str, object],
    font_png: Path,
) -> dict[str, object]:
    with Image.open(reference_path) as reference:
        if reference.mode != "P":
            raise ValueError(f"reference must be indexed PNG: {reference_path}")
        width, height = reference.size
        reference_values = list(reference.get_flattened_data())
        palette = reference.getpalette()
    values = list(reference_values)
    operations: list[dict[str, object]] = []
    declared_indices: set[int] = set()
    allowed_change_positions: set[int] = set()

    for restore in spec.get("restore_rows", []):
        box = list(restore["box"])
        sample_box = list(restore["sample_box"])
        explicit_row_indices = [
            int(value) for value in restore.get("row_background_indices", [])
        ]
        only_indices = {int(value) for value in restore.get("only_indices", [])}
        if explicit_row_indices:
            x0, y0, x1, y1 = v19.box_edges(box)
            if len(explicit_row_indices) != y1 - y0:
                raise ValueError(
                    f"row_background_indices height mismatch: {canvas_id}"
                )
            for y, background in zip(
                range(y0, y1), explicit_row_indices, strict=True
            ):
                for x in range(x0, x1):
                    values[y * width + x] = background
            row_indices = explicit_row_indices
            allowed_change_positions.update(
                y * width + x for y in range(y0, y1) for x in range(x0, x1)
            )
        elif only_indices:
            row_indices, restored_positions = restore_selected_indices(
                values,
                reference_values,
                width,
                box,
                sample_box,
                str(restore.get("method", "mode")),
                only_indices,
                {int(value) for value in restore.get("ignore_indices", [])},
            )
            allowed_change_positions.update(restored_positions)
        else:
            row_indices = v19.restore_rows_from_reference(
                values,
                reference_values,
                width,
                box,
                sample_box,
                str(restore.get("method", "max")),
            )
            x0, y0, x1, y1 = v19.box_edges(box)
            allowed_change_positions.update(
                y * width + x for y in range(y0, y1) for x in range(x0, x1)
            )
        declared_indices.update(row_indices)
        operations.append(
            {
                "kind": (
                    "restore_explicit_row_backgrounds"
                    if explicit_row_indices
                    else "restore_selected_indices"
                    if only_indices
                    else "restore_rows"
                ),
                "box": box,
                "sample_box": sample_box,
                "method": restore.get("method", "max"),
                "row_indices": row_indices,
                "only_indices": sorted(only_indices),
                "explicit_row_backgrounds": bool(explicit_row_indices),
                "preserved_outside_selected_indices": bool(only_indices),
            }
        )

    for label in spec["labels"]:
        text = str(label["text"])
        mask = compose_bitmap_mask(font_png, text)
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
            allowed_change_positions.update(
                mask_positions(
                    mask,
                    (
                        float(label["center"][0]) + int(offset_x),
                        float(label["center"][1]) + int(offset_y),
                    ),
                    width,
                )
            )
            declared_indices.add(shadow_index)
            operations.append(
                {
                    "kind": "shadow",
                    "text": text,
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
        allowed_change_positions.update(
            mask_positions(mask, tuple(label["center"]), width)
        )
        if not v19.inside(output_box[:4], list(label["bounds_box"])):
            raise ValueError(f"Galmuri7 label exceeds bounds: {canvas_id}")
        declared_indices.add(ink_index)
        operations.append(
            {
                "kind": "bitmap_atlas_text",
                "text": text,
                "font_role": "galmuri7_native_8x8",
                "cell_size": [8, 8],
                "center": label["center"],
                "output_box": output_box,
                "ink_index": ink_index,
            }
        )

    source_indices = set(reference_values)
    if not declared_indices <= source_indices or not set(values) <= source_indices:
        raise ValueError(f"Galmuri7 label uses foreign palette indices: {canvas_id}")
    change_boxes = [list(box) for box in spec["change_boxes"]]
    changed = [
        position
        for position, pair in enumerate(zip(reference_values, values, strict=True))
        if pair[0] != pair[1]
    ]
    if not v19.changed_inside_boxes(changed, width, change_boxes):
        raise ValueError(f"Galmuri7 label escaped registered boxes: {canvas_id}")
    escaped_protected_pixels = [
        position for position in changed if position not in allowed_change_positions
    ]
    if escaped_protected_pixels:
        raise ValueError(
            f"Galmuri7 label changed protected pixels: {canvas_id}: "
            f"{escaped_protected_pixels[:8]}"
        )

    indexed_path, preview_path = v21.save_canvas(
        output_root, canvas_id, values, width, height, palette
    )
    return v21.common_row(
        canvas_id,
        "galmuri7_native_8x8_system_label",
        operations,
        change_boxes,
        reference_values,
        values,
        output_root,
        indexed_path,
        preview_path,
    )


def build(args: argparse.Namespace) -> dict[str, object]:
    if args.output_root.exists() and any(args.output_root.iterdir()):
        raise FileExistsError(f"refusing nonempty output root: {args.output_root}")
    base_report = json.loads((args.base_root / "report.json").read_text(encoding="utf-8"))
    graphics_config = json.loads(args.graphics_config.read_text(encoding="utf-8"))
    profile_bytes = args.profile.read_bytes()
    profile = json.loads(profile_bytes.decode("utf-8"))
    font_png = (args.profile.parent / profile["font"]["path"]).resolve()
    font_bytes = font_png.read_bytes()
    if v19.sha256(font_bytes) != profile["font"]["sha256"]:
        raise ValueError("Galmuri7 font hash mismatch")

    indexed_dir = args.output_root / "indexed"
    preview_dir = args.output_root / "preview_x4"
    indexed_dir.mkdir(parents=True)
    preview_dir.mkdir(parents=True)
    for source in (args.base_root / "indexed").glob("*.png"):
        shutil.copy2(source, indexed_dir / source.name)
    for source in (args.base_root / "preview_x4").glob("*.png"):
        shutil.copy2(source, preview_dir / source.name)

    target_ids = set(profile["targets"])
    target_specs = graphics_config["canvases"]
    rebuilt = {
        canvas_id: rebuild_label(
            canvas_id,
            args.reference_dir / f"{canvas_id}.png",
            args.output_root,
            target_specs[canvas_id],
            font_png,
        )
        for canvas_id in sorted(target_ids)
    }
    base_canvas_ids = {row["canvas_id"] for row in base_report["outputs"]}
    rows = [rebuilt.get(row["canvas_id"], row) for row in base_report["outputs"]]
    rows.extend(
        rebuilt[canvas_id]
        for canvas_id in sorted(target_ids - base_canvas_ids)
    )
    v19.write_contact_sheet(args.output_root, rows)

    result = dict(base_report)
    result["status"] = "galmuri7_system_labels_image_preview_static_only"
    result["base_image_set"] = {
        "path": str(args.base_root),
        "report_sha256": v19.sha256((args.base_root / "report.json").read_bytes()),
    }
    result["system_label_profile"] = {
        "config_path": str(args.profile),
        "config_sha256": v19.sha256(profile_bytes),
        "font_path": str(font_png),
        "font_sha256": v19.sha256(font_bytes),
        "render_mode": "native_bitmap_cell",
        "targets": sorted(target_ids),
        "labels": profile["labels"],
        "scope": "image_preview_only_no_rom_build",
    }
    result["outputs"] = rows
    result["image_count"] = len(rows)
    result["rebuilt_count"] = len(rows)
    result["not_proved"] = [
        "runtime palette and layer composition",
        "emulator rendering of the rebuilt state assets",
        "whole-game behavior",
    ]
    (args.output_root / "report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "status": result["status"],
        "target_count": len(target_ids),
        "output_root": str(args.output_root),
        "font_sha256": result["system_label_profile"]["font_sha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-root", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--graphics-config", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
