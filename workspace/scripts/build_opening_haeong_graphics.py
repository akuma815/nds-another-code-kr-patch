#!/usr/bin/env python3
"""Create a full indexed graphics set with HaeongSans opening captions."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
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


def validate_declared_indices(
    reference_values: list[int], declared_indices: set[int], canvas_id: str
) -> None:
    foreign = sorted(declared_indices - set(reference_values))
    if foreign:
        raise ValueError(
            f"configured palette indices are absent from the source resource: "
            f"{canvas_id} {foreign}"
        )


def dilate_binary_mask(mask: Image.Image, radius: int) -> Image.Image:
    if radius < 1:
        raise ValueError("outline radius must be positive")
    padded = Image.new("L", (mask.width + radius * 2, mask.height + radius * 2), 0)
    padded.paste(mask.convert("L"), (radius, radius))
    dilated = padded.filter(ImageFilter.MaxFilter(radius * 2 + 1))
    return dilated.point(lambda value: 255 if value else 0, mode="1")


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
        raise ValueError("opening font hash mismatch")
    threshold = int(font_profile["binary_threshold"])

    base_paths = {path.stem: path for path in args.base_indexed_dir.glob("*.png")}
    reference_paths = {path.stem: path for path in args.reference_dir.glob("*.png")}
    if not base_paths or set(base_paths) != set(reference_paths):
        raise ValueError("base/reference indexed populations differ")
    targets = graphics_config["canvases"]
    retained = set(graphics_config["retained_existing_graphics"])
    if set(targets) & retained:
        raise ValueError("target and retained populations overlap")
    if set(targets) | retained != set(base_paths):
        missing = sorted(set(base_paths) - (set(targets) | retained))
        unknown = sorted((set(targets) | retained) - set(base_paths))
        raise ValueError(f"graphics population contract mismatch: missing={missing}, unknown={unknown}")

    indexed_dir = args.output_root / "indexed"
    preview_dir = args.output_root / "preview_x4"
    indexed_dir.mkdir(parents=True)
    preview_dir.mkdir()
    rows: list[dict[str, object]] = []
    for canvas_id in sorted(base_paths):
        indexed_path = indexed_dir / f"{canvas_id}.png"
        operations: list[dict[str, object]] = []
        if canvas_id in retained:
            shutil.copyfile(base_paths[canvas_id], indexed_path)
            action = "retained_existing_indexed_graphic"
        else:
            spec = targets[canvas_id]
            with Image.open(reference_paths[canvas_id]) as reference:
                if reference.mode != "P":
                    raise ValueError(f"reference must be indexed: {canvas_id}")
                width, height = reference.size
                reference_values = list(reference.get_flattened_data())
                palette = reference.getpalette()
            values = list(reference_values)
            cleanup_box = list(spec["cleanup_box"])
            background_index = int(spec["background_index"])
            ink_index = int(spec["ink_index"])
            outline_index = int(spec["outline_index"])
            outline_radius = int(spec.get("outline_radius", 1))
            validate_declared_indices(
                reference_values,
                {background_index, outline_index, ink_index},
                canvas_id,
            )
            fill_box(values, width, cleanup_box, background_index)
            operations.append(
                {
                    "kind": "cleanup",
                    "box": cleanup_box,
                    "background_index": background_index,
                }
            )
            for label in spec["labels"]:
                font = ImageFont.truetype(str(font_path), int(label["size"]))
                mask = compose_ttf_text_mask(font, str(label["text"]), threshold)
                outline_mask = dilate_binary_mask(mask, outline_radius)
                outline_box = paste_mask(
                    values,
                    width,
                    height,
                    outline_mask,
                    tuple(label["center"]),
                    outline_index,
                )
                if not inside(outline_box[:4], cleanup_box):
                    raise ValueError(
                        f"outline exceeds cleanup box: {canvas_id} {outline_box}"
                    )
                output_box = paste_mask(
                    values,
                    width,
                    height,
                    mask,
                    tuple(label["center"]),
                    ink_index,
                )
                if not inside(output_box[:4], cleanup_box):
                    raise ValueError(f"label exceeds cleanup box: {canvas_id} {output_box}")
                operations.append(
                    {
                        "kind": "binary_ttf_text",
                        "text": label["text"],
                        "font_size": label["size"],
                        "center": label["center"],
                        "outline_box": outline_box,
                        "outline_index": outline_index,
                        "outline_radius": outline_radius,
                        "output_box": output_box,
                        "ink_index": ink_index,
                    }
                )
            changed = [
                index
                for index, pair in enumerate(zip(reference_values, values))
                if pair[0] != pair[1]
            ]
            if not changed:
                raise ValueError(f"opening canvas produced no change: {canvas_id}")
            x0, y0, x1, y1 = box_edges(cleanup_box)
            for position in changed:
                x, y = position % width, position // width
                if not (x0 <= x < x1 and y0 <= y < y1):
                    raise ValueError(f"change escaped cleanup box: {canvas_id}")
            changed_values = {values[index] for index in changed}
            if not changed_values <= {background_index, outline_index, ink_index}:
                raise ValueError(f"undeclared output index: {canvas_id}")
            with indexed_image(values, width, height) as output:
                if palette is not None:
                    output.putpalette(palette)
                output.save(indexed_path, format="PNG", optimize=False)
            action = "haeong_binary_ttf_with_source_palette_outline_rebuilt_from_reference"

        with Image.open(indexed_path) as indexed:
            if indexed.mode != "P":
                raise ValueError(f"output is not indexed: {canvas_id}")
            width, height = indexed.size
            indices = list(indexed.get_flattened_data())
        preview_path = preview_dir / f"{canvas_id}.png"
        with render_index_preview(indices, width, height) as preview:
            preview.resize((width * 4, height * 4), Image.Resampling.NEAREST).save(
                preview_path, format="PNG", optimize=False
            )
        rows.append(
            {
                "canvas_id": canvas_id,
                "action": action,
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
        "status": "full_graphics_image_set_with_haeong_opening_prepared_static_only",
        "graphics_config": {
            "path": str(args.graphics_config),
            "sha256": sha256(graphics_config_bytes),
        },
        "font_config": {
            "path": str(font_config_path),
            "sha256": sha256(font_config_bytes),
        },
        "font": {"path": str(font_path), "sha256": sha256(font_bytes), **font_profile},
        "base_indexed_dir": str(args.base_indexed_dir),
        "reference_dir": str(args.reference_dir),
        "image_count": len(rows),
        "opening_rebuilt_count": len(targets),
        "retained_count": len(retained),
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
    parser.add_argument("--base-indexed-dir", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--graphics-config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
