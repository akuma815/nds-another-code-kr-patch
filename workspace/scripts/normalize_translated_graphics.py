#!/usr/bin/env python3
"""Normalize edited greyscale previews to reversible indexed edit canvases."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw

from build_graphics_edit_canvases import indexed_image
from render_pack_index_previews import render_index_preview


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def rank_levels(indices: list[int]) -> dict[int, int]:
    used = sorted(set(indices))
    if len(used) == 1:
        return {used[0]: 0}
    return {
        value: round(rank * 255 / (len(used) - 1))
        for rank, value in enumerate(used)
    }


def downsample_uniform_blocks(
    image: Image.Image, target_size: tuple[int, int]
) -> tuple[list[int], int]:
    target_w, target_h = target_size
    if image.width % target_w or image.height % target_h:
        raise ValueError(
            f"input size {image.size} is not an integer scale of {target_size}"
        )
    scale_x, scale_y = image.width // target_w, image.height // target_h
    if scale_x != scale_y or scale_x < 1:
        raise ValueError(
            f"input requires equal positive integer scale, got {scale_x}x{scale_y}"
        )
    grey = image.convert("L")
    pixels = list(grey.get_flattened_data())
    output: list[int] = []
    for target_y in range(target_h):
        source_y = target_y * scale_y
        for target_x in range(target_w):
            source_x = target_x * scale_x
            first = pixels[source_y * image.width + source_x]
            for local_y in range(scale_y):
                start = (source_y + local_y) * image.width + source_x
                if any(value != first for value in pixels[start : start + scale_x]):
                    raise ValueError(
                        f"nonuniform source block at ({source_x},{source_y})"
                    )
            output.append(first)
    return output, scale_x


def map_greys_to_indices(
    greys: list[int], levels: dict[int, int]
) -> tuple[list[int], list[int]]:
    candidates = sorted(levels.items(), key=lambda item: (item[1], item[0]))
    mapped: list[int] = []
    errors: list[int] = []
    for grey in greys:
        index, level = min(
            candidates, key=lambda item: (abs(item[1] - grey), item[1], item[0])
        )
        mapped.append(index)
        errors.append(abs(level - grey))
    return mapped, errors


def changed_bbox(
    reference: list[int], translated: list[int], width: int
) -> tuple[int, int, int, int] | None:
    changed = [index for index, pair in enumerate(zip(reference, translated)) if pair[0] != pair[1]]
    if not changed:
        return None
    xs = [index % width for index in changed]
    ys = [index // width for index in changed]
    return min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


def write_contact_sheet(output_root: Path, rows: list[dict[str, object]]) -> str:
    columns = 3
    cell_width, cell_height = 276, 222
    sheet_rows = math.ceil(len(rows) / columns)
    sheet = Image.new(
        "RGB", (cell_width * columns, cell_height * sheet_rows), (32, 32, 32)
    )
    draw = ImageDraw.Draw(sheet)
    for position, row in enumerate(rows):
        x = (position % columns) * cell_width + 10
        y = (position // columns) * cell_height + 20
        with Image.open(output_root / str(row["preview_png"])) as source:
            preview = source.convert("RGB")
            preview.thumbnail((256, 192), Image.Resampling.NEAREST)
        sheet.paste(preview, (x, y))
        draw.text((x, y - 15), str(row["canvas_id"]), fill=(255, 255, 0))
    rel = "contact_sheet.png"
    sheet.save(output_root / rel, format="PNG", optimize=False)
    return rel


def build(translation_dir: Path, reference_dir: Path, output_root: Path) -> dict[str, object]:
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty output root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    translation_paths = {path.name: path for path in translation_dir.glob("*.png")}
    reference_paths = {path.name: path for path in reference_dir.glob("*.png")}
    if not translation_paths:
        raise ValueError(f"no PNG files found: {translation_dir}")
    unknown = sorted(set(translation_paths) - set(reference_paths))
    missing = sorted(set(reference_paths) - set(translation_paths))
    if unknown or missing:
        raise ValueError(f"image population mismatch: unknown={unknown}, missing={missing}")

    rows: list[dict[str, object]] = []
    for name in sorted(translation_paths):
        translated_path = translation_paths[name]
        reference_path = reference_paths[name]
        with Image.open(reference_path) as reference_image:
            if reference_image.mode != "P":
                raise ValueError(f"reference must be P mode: {reference_path}")
            width, height = reference_image.size
            reference_indices = list(reference_image.get_flattened_data())
        with Image.open(translated_path) as translated_image:
            source_mode = translated_image.mode
            source_size = translated_image.size
            greys, scale = downsample_uniform_blocks(
                translated_image, (width, height)
            )
        levels = rank_levels(reference_indices)
        mapped, errors = map_greys_to_indices(greys, levels)
        indexed_rel = Path("indexed") / name
        indexed_path = output_root / indexed_rel
        indexed_path.parent.mkdir(parents=True, exist_ok=True)
        with indexed_image(mapped, width, height) as image:
            image.save(indexed_path, format="PNG", optimize=False)
        preview_rel = Path("preview_x4") / name
        preview_path = output_root / preview_rel
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        with render_index_preview(mapped, width, height) as preview:
            preview.resize((width * 4, height * 4), Image.Resampling.NEAREST).save(
                preview_path, format="PNG", optimize=False
            )
        bbox = changed_bbox(reference_indices, mapped, width)
        rows.append(
            {
                "canvas_id": translated_path.stem,
                "translation_source": str(translated_path),
                "translation_source_sha256": sha256(translated_path.read_bytes()),
                "source_mode": source_mode,
                "source_size": list(source_size),
                "target_size": [width, height],
                "integer_scale": scale,
                "uniform_block_downsample": "pass",
                "reference_index_count": len(set(reference_indices)),
                "output_index_count": len(set(mapped)),
                "exact_level_pixel_count": sum(error == 0 for error in errors),
                "quantized_pixel_count": sum(error != 0 for error in errors),
                "max_greyscale_error": max(errors, default=0),
                "mean_greyscale_error": round(sum(errors) / len(errors), 6),
                "changed_pixel_count": sum(
                    before != after
                    for before, after in zip(reference_indices, mapped)
                ),
                "changed_bbox": list(bbox) if bbox else None,
                "indexed_png": str(indexed_rel),
                "indexed_png_sha256": sha256(indexed_path.read_bytes()),
                "preview_png": str(preview_rel),
                "preview_png_sha256": sha256(preview_path.read_bytes()),
            }
        )
    contact_sheet = write_contact_sheet(output_root, rows)
    result = {
        "schema_version": 1,
        "status": "translated_graphics_normalized_to_indexed_edit_canvases",
        "translation_dir": str(translation_dir),
        "reference_dir": str(reference_dir),
        "image_count": len(rows),
        "uniform_block_downsample_pass_count": len(rows),
        "contact_sheet": contact_sheet,
        "outputs": rows,
        "not_proved": [
            "translation wording or removal of every source-language pixel",
            "runtime palette colours and layer composition",
            "pack reinsertion and compression compatibility",
            "emulator playback of edited graphics",
        ],
    }
    (output_root / "report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--translation-dir", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.translation_dir, args.reference_dir, args.output_root)
    print(
        json.dumps(
            {
                "status": result["status"],
                "image_count": result["image_count"],
                "uniform_block_downsample_pass_count": result[
                    "uniform_block_downsample_pass_count"
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
