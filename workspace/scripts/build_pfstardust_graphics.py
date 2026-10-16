#!/usr/bin/env python3
"""Rebuild every baked Korean graphic with the selected PF Stardust font."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from PIL import Image, ImageFilter, ImageFont

from build_graphics_edit_canvases import indexed_image
from build_opening_1948_font_poc import box_edges, fill_box, paste_mask
from build_opening_1948_ttf_poc import compose_ttf_text_mask
from refine_translated_graphics import write_contact_sheet
from render_pack_index_previews import render_index_preview


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def inside(inner: list[int], outer: list[int]) -> bool:
    ix0, iy0, ix1, iy1 = box_edges(inner)
    ox0, oy0, ox1, oy1 = box_edges(outer)
    return ox0 <= ix0 <= ix1 <= ox1 and oy0 <= iy0 <= iy1 <= oy1


def validate_box(box: list[int], width: int, height: int, label: str) -> None:
    x0, y0, x1, y1 = box_edges(box)
    if x0 < 0 or y0 < 0 or x1 > width or y1 > height or x0 >= x1 or y0 >= y1:
        raise ValueError(f"invalid {label} box: {box} for {width}x{height}")


def dilate_binary_mask(mask: Image.Image, radius: int) -> Image.Image:
    if radius < 1:
        raise ValueError("outline radius must be positive")
    padded = Image.new("L", (mask.width + radius * 2, mask.height + radius * 2), 0)
    padded.paste(mask.convert("L"), (radius, radius))
    dilated = padded.filter(ImageFilter.MaxFilter(radius * 2 + 1))
    return dilated.point(lambda value: 255 if value else 0, mode="1")


def restore_rows_from_reference(
    values: list[int],
    reference_values: list[int],
    width: int,
    box: list[int],
    sample_box: list[int],
    method: str,
) -> list[int]:
    x0, y0, x1, y1 = box_edges(box)
    sx0, sy0, sx1, sy1 = box_edges(sample_box)
    if (y0, y1) != (sy0, sy1):
        raise ValueError("restore and sample boxes must cover identical rows")
    restored: list[int] = []
    for y in range(y0, y1):
        samples = reference_values[y * width + sx0 : y * width + sx1]
        if method == "max":
            background = max(samples)
        elif method == "mode":
            background = Counter(samples).most_common(1)[0][0]
        elif method == "median":
            ordered = sorted(samples)
            background = ordered[len(ordered) // 2]
        else:
            raise ValueError(f"unsupported restore method: {method}")
        values[y * width + x0 : y * width + x1] = [background] * (x1 - x0)
        restored.append(background)
    return restored


def copy_box(
    values: list[int], reference_values: list[int], width: int, box: list[int]
) -> None:
    x0, y0, x1, y1 = box_edges(box)
    for y in range(y0, y1):
        start = y * width + x0
        end = y * width + x1
        values[start:end] = reference_values[start:end]


def remove_connected_components(
    values: list[int],
    width: int,
    height: int,
    search_box: list[int],
    background_index: int,
    max_pixels: int,
) -> list[dict[str, object]]:
    """Erase isolated baked-text components without touching connected frames.

    Components are traced across the complete canvas, then removed only when
    their full bounds stay inside ``search_box``.  This protects ornate room
    card borders, birds, and flourishes even when they overlap the text band.
    """
    x0, y0, x1, y1 = box_edges(search_box)
    visited: set[int] = set()
    removed: list[dict[str, object]] = []
    for seed_y in range(y0, y1):
        for seed_x in range(x0, x1):
            seed = seed_y * width + seed_x
            if seed in visited or values[seed] == background_index:
                continue
            stack = [seed]
            visited.add(seed)
            component: list[int] = []
            while stack:
                position = stack.pop()
                component.append(position)
                x, y = position % width, position // width
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if not (0 <= nx < width and 0 <= ny < height):
                        continue
                    neighbor = ny * width + nx
                    if neighbor in visited or values[neighbor] == background_index:
                        continue
                    visited.add(neighbor)
                    stack.append(neighbor)
            xs = [position % width for position in component]
            ys = [position // width for position in component]
            bounds = [min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1]
            bx0, by0, bx1, by1 = box_edges(bounds)
            if (
                x0 <= bx0
                and y0 <= by0
                and bx1 <= x1
                and by1 <= y1
                and len(component) <= max_pixels
            ):
                for position in component:
                    values[position] = background_index
                removed.append({"box": bounds, "pixel_count": len(component)})
    return removed


def draw_line(values: list[int], width: int, box: list[int], index: int) -> None:
    fill_box(values, width, box, index)


def changed_inside_boxes(
    changed: list[int], width: int, boxes: list[list[int]]
) -> bool:
    edges = [box_edges(box) for box in boxes]
    for position in changed:
        x, y = position % width, position // width
        if not any(x0 <= x < x1 and y0 <= y < y1 for x0, y0, x1, y1 in edges):
            return False
    return True


def build(args: argparse.Namespace) -> dict[str, object]:
    if args.output_root.exists() and any(args.output_root.iterdir()):
        raise FileExistsError(f"refusing nonempty output root: {args.output_root}")

    graphics_config_bytes = args.graphics_config.read_bytes()
    graphics_config = json.loads(graphics_config_bytes.decode("utf-8"))
    font_config_path = args.graphics_config.parent / graphics_config["font_config"]
    font_config_bytes = font_config_path.read_bytes()
    font_config = json.loads(font_config_bytes.decode("utf-8"))
    font_profile = font_config["font"]
    font_path = (font_config_path.parent / font_profile["path"]).resolve()
    font_bytes = font_path.read_bytes()
    if sha256(font_bytes) != font_profile["sha256"]:
        raise ValueError("PF Stardust font hash mismatch")
    threshold = int(font_profile["binary_threshold"])

    reference_paths = {path.stem: path for path in args.reference_dir.glob("*.png")}
    targets = graphics_config["canvases"]
    if not reference_paths or set(reference_paths) != set(targets):
        missing = sorted(set(reference_paths) - set(targets))
        unknown = sorted(set(targets) - set(reference_paths))
        raise ValueError(
            f"graphics population contract mismatch: missing={missing}, unknown={unknown}"
        )

    indexed_dir = args.output_root / "indexed"
    preview_dir = args.output_root / "preview_x4"
    indexed_dir.mkdir(parents=True)
    preview_dir.mkdir()
    rows: list[dict[str, object]] = []

    for canvas_id in sorted(reference_paths):
        spec = targets[canvas_id]
        with Image.open(reference_paths[canvas_id]) as reference:
            if reference.mode != "P":
                raise ValueError(f"reference must be indexed: {canvas_id}")
            width, height = reference.size
            reference_values = list(reference.get_flattened_data())
            palette = reference.getpalette()
        source_indices = set(reference_values)
        values = list(reference_values)
        operations: list[dict[str, object]] = []
        change_boxes = [list(box) for box in spec["change_boxes"]]
        for box in change_boxes:
            validate_box(box, width, height, f"{canvas_id} change")

        declared_indices: set[int] = set()
        for cleanup in spec.get("remove_components", []):
            search_box = list(cleanup["search_box"])
            validate_box(search_box, width, height, f"{canvas_id} component search")
            background_index = int(cleanup["background_index"])
            max_pixels = int(cleanup.get("max_pixels", 512))
            if max_pixels < 1:
                raise ValueError("component max_pixels must be positive")
            declared_indices.add(background_index)
            removed = remove_connected_components(
                values,
                width,
                height,
                search_box,
                background_index,
                max_pixels,
            )
            if not removed:
                raise ValueError(f"no removable components found: {canvas_id}")
            operations.append(
                {
                    "kind": "remove_connected_components",
                    "search_box": search_box,
                    "background_index": background_index,
                    "max_pixels": max_pixels,
                    "removed": removed,
                }
            )
        for cleanup in spec.get("fill_boxes", []):
            box = list(cleanup["box"])
            validate_box(box, width, height, f"{canvas_id} fill")
            background_index = int(cleanup["background_index"])
            declared_indices.add(background_index)
            fill_box(values, width, box, background_index)
            operations.append(
                {"kind": "fill_box", "box": box, "background_index": background_index}
            )

        for restore in spec.get("restore_rows", []):
            box = list(restore["box"])
            sample_box = list(restore["sample_box"])
            validate_box(box, width, height, f"{canvas_id} restore")
            validate_box(sample_box, width, height, f"{canvas_id} sample")
            method = str(restore.get("method", "max"))
            row_indices = restore_rows_from_reference(
                values, reference_values, width, box, sample_box, method
            )
            declared_indices.update(row_indices)
            operations.append(
                {
                    "kind": "restore_rows",
                    "box": box,
                    "sample_box": sample_box,
                    "method": method,
                    "row_indices": row_indices,
                }
            )

        for preserve in spec.get("preserve_boxes", []):
            box = list(preserve["box"])
            validate_box(box, width, height, f"{canvas_id} preserve")
            copy_box(values, reference_values, width, box)
            operations.append({"kind": "preserve_box", "box": box})

        for line in spec.get("lines", []):
            box = list(line["box"])
            validate_box(box, width, height, f"{canvas_id} line")
            index = int(line["index"])
            declared_indices.add(index)
            draw_line(values, width, box, index)
            operations.append({"kind": "line", "box": box, "index": index})

        for label in spec.get("labels", []):
            ink_index = int(label["ink_index"])
            declared_indices.add(ink_index)
            font = ImageFont.truetype(str(font_path), int(label["size"]))
            mask = compose_ttf_text_mask(font, str(label["text"]), threshold)

            shadow = label.get("shadow")
            if shadow:
                shadow_index = int(shadow["index"])
                offset_x, offset_y = shadow["offset"]
                declared_indices.add(shadow_index)
                shadow_box = paste_mask(
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
                operations.append(
                    {
                        "kind": "shadow",
                        "text": label["text"],
                        "box": shadow_box,
                        "index": shadow_index,
                    }
                )

            outline_index = label.get("outline_index")
            if outline_index is not None:
                outline_index = int(outline_index)
                outline_radius = int(label.get("outline_radius", 1))
                declared_indices.add(outline_index)
                outline_mask = dilate_binary_mask(mask, outline_radius)
                outline_box = paste_mask(
                    values,
                    width,
                    height,
                    outline_mask,
                    tuple(label["center"]),
                    outline_index,
                )
                operations.append(
                    {
                        "kind": "outline",
                        "text": label["text"],
                        "box": outline_box,
                        "index": outline_index,
                        "radius": outline_radius,
                    }
                )

            output_box = paste_mask(
                values,
                width,
                height,
                mask,
                tuple(label["center"]),
                ink_index,
            )
            bounds_box = list(label["bounds_box"])
            if not inside(output_box[:4], bounds_box):
                raise ValueError(
                    f"label exceeds declared bounds: {canvas_id} {label['text']} {output_box}"
                )
            operations.append(
                {
                    "kind": "binary_ttf_text",
                    "text": label["text"],
                    "font_size": label["size"],
                    "center": label["center"],
                    "output_box": output_box,
                    "ink_index": ink_index,
                }
            )

        foreign = sorted(declared_indices - source_indices)
        if foreign:
            raise ValueError(
                f"configured palette indices absent from source: {canvas_id} {foreign}"
            )
        changed = [
            position
            for position, pair in enumerate(zip(reference_values, values))
            if pair[0] != pair[1]
        ]
        if not changed:
            raise ValueError(f"canvas produced no change: {canvas_id}")
        if not changed_inside_boxes(changed, width, change_boxes):
            raise ValueError(f"change escaped registered boxes: {canvas_id}")
        if not set(values).issubset(source_indices):
            raise ValueError(f"output introduces foreign palette indices: {canvas_id}")

        indexed_path = indexed_dir / f"{canvas_id}.png"
        with indexed_image(values, width, height) as output:
            if palette is not None:
                output.putpalette(palette)
            output.save(indexed_path, format="PNG", optimize=False)
        preview_path = preview_dir / f"{canvas_id}.png"
        with render_index_preview(values, width, height) as preview:
            preview.resize((width * 4, height * 4), Image.Resampling.NEAREST).save(
                preview_path, format="PNG", optimize=False
            )
        rows.append(
            {
                "canvas_id": canvas_id,
                "action": "pf_stardust_binary_ttf_rebuilt_from_source_indexed_canvas",
                "changed_pixel_count": len(changed),
                "change_boxes": change_boxes,
                "operations": operations,
                "indexed_png": str(indexed_path.relative_to(args.output_root)),
                "indexed_png_sha256": sha256(indexed_path.read_bytes()),
                "preview_png": str(preview_path.relative_to(args.output_root)),
                "preview_png_sha256": sha256(preview_path.read_bytes()),
            }
        )

    contact_sheet = write_contact_sheet(args.output_root, rows)
    result = {
        "schema_version": 1,
        "status": "full_graphics_set_rebuilt_with_pf_stardust_static_only",
        "graphics_config": {
            "path": str(args.graphics_config),
            "sha256": sha256(graphics_config_bytes),
        },
        "font_config": {
            "path": str(font_config_path),
            "sha256": sha256(font_config_bytes),
        },
        "font": {"path": str(font_path), "sha256": sha256(font_bytes), **font_profile},
        "reference_dir": str(args.reference_dir),
        "image_count": len(rows),
        "rebuilt_count": len(rows),
        "contact_sheet": contact_sheet,
        "outputs": rows,
        "not_proved": [
            "ROM reinsertion",
            "runtime palette and layer composition",
            "emulator playback",
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
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--graphics-config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
