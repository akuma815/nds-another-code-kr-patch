#!/usr/bin/env python3
"""Match translated graphic backgrounds and text scale to the source canvases."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from build_graphics_edit_canvases import indexed_image
from normalize_translated_graphics import (
    downsample_uniform_blocks,
    map_greys_to_indices,
    rank_levels,
)
from render_pack_index_previews import render_index_preview


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def reference_rank_greys(indices: list[int]) -> tuple[dict[int, int], list[int]]:
    levels = rank_levels(indices)
    return levels, [levels[index] for index in indices]


def constrain_box_indices(
    mapped: list[int],
    errors: list[int],
    greys: list[int],
    width: int,
    height: int,
    box: list[int],
    levels: dict[int, int],
    allowed_indices: list[int],
) -> dict[str, object]:
    """Quantize one visual role only to its declared runtime palette indices."""
    x0, y0, x1, y1 = box_edges(box)
    if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
        raise ValueError(f"index constraint exceeds canvas: {box}")
    allowed = sorted(set(int(index) for index in allowed_indices))
    if not allowed:
        raise ValueError("index constraint requires at least one allowed index")
    unknown = [index for index in allowed if index not in levels]
    if unknown:
        raise ValueError(f"index constraint uses absent reference indices: {unknown}")
    candidates = [(index, levels[index]) for index in allowed]
    before: set[int] = set()
    after: set[int] = set()
    changed = 0
    max_error = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            position = y * width + x
            before.add(mapped[position])
            index, level = min(
                candidates,
                key=lambda item: (abs(item[1] - greys[position]), item[1], item[0]),
            )
            if mapped[position] != index:
                changed += 1
            mapped[position] = index
            errors[position] = abs(level - greys[position])
            max_error = max(max_error, errors[position])
            after.add(index)
    return {
        "box": box,
        "allowed_indices": allowed,
        "indices_before": sorted(before),
        "indices_after": sorted(after),
        "remapped_pixel_count": changed,
        "max_greyscale_error": max_error,
    }


def box_edges(box: list[int]) -> tuple[int, int, int, int]:
    x, y, width, height = box
    return x, y, x + width, y + height


def fill_box(values: list[int], width: int, box: list[int], value: int) -> None:
    x0, y0, x1, y1 = box_edges(box)
    for y in range(y0, y1):
        values[y * width + x0 : y * width + x1] = [value] * (x1 - x0)


def fill_rows_from_reference(
    values: list[int],
    width: int,
    reference_values: list[int],
    box: list[int],
    sample_box: list[int],
    method: str = "max",
) -> list[int]:
    """Replace each target row with a matching reference background value."""
    x0, y0, x1, y1 = box_edges(box)
    sample_x0, sample_y0, sample_x1, sample_y1 = box_edges(sample_box)
    if (y0, y1) != (sample_y0, sample_y1):
        raise ValueError("restore and sample boxes must cover the same rows")
    row_values: list[int] = []
    for y in range(y0, y1):
        samples = reference_values[y * width + sample_x0 : y * width + sample_x1]
        if method == "max":
            background = max(samples)
        elif method == "median":
            ordered = sorted(samples)
            background = ordered[len(ordered) // 2]
        else:
            raise ValueError(f"unknown row restore method: {method}")
        values[y * width + x0 : y * width + x1] = [background] * (x1 - x0)
        row_values.append(background)
    return row_values


def draw_font_text(
    values: list[int],
    width: int,
    height: int,
    operation: dict[str, object],
    config_dir: Path,
) -> list[int]:
    image = Image.new("L", (width, height))
    image.putdata(values)
    draw = ImageDraw.Draw(image)
    font_path = (config_dir / str(operation["font_path"])).resolve()
    font = ImageFont.truetype(str(font_path), int(operation["font_size"]))
    text = str(operation["text"])
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    center_x, center_y = operation["center"]
    left = round(float(center_x) - text_width / 2 - bbox[0])
    top = round(float(center_y) - text_height / 2 - bbox[1])
    shadow = operation.get("shadow")
    if shadow:
        offset_x, offset_y = shadow["offset"]
        draw.text(
            (left + int(offset_x), top + int(offset_y)),
            text,
            font=font,
            fill=int(shadow["value"]),
        )
    draw.text((left, top), text, font=font, fill=int(operation["value"]))
    values[:] = list(image.get_flattened_data())
    return [left + bbox[0], top + bbox[1], text_width, text_height]


def resize_delta_for_target(
    delta_image: Image.Image,
    target_center: list[float],
    target_height: int,
    width: int,
    height: int,
) -> tuple[Image.Image, int, int]:
    target_width = max(1, round(delta_image.width * target_height / delta_image.height))
    resized = delta_image.resize((target_width, target_height), Image.Resampling.LANCZOS)
    center_x, center_y = target_center
    left = round(center_x - target_width / 2)
    top = round(center_y - target_height / 2)
    if left < 0 or top < 0 or left + target_width > width or top + target_height > height:
        raise ValueError("resized text exceeds canvas")
    return resized, left, top


def draw_horizontal_lines(
    values: list[int],
    width: int,
    lines: list[dict[str, object]],
    avoid_mask: list[int] | None = None,
) -> int:
    written = 0
    for line in lines:
        x0, y0, x1, y1 = box_edges(list(line["box"]))
        value = int(line["value"])
        for y in range(y0, y1):
            for x in range(x0, x1):
                position = y * width + x
                if avoid_mask is not None and avoid_mask[position]:
                    continue
                values[position] = min(values[position], value)
                written += 1
    return written


def compose_line_aware_text(
    values: list[int],
    width: int,
    height: int,
    delta_image: Image.Image,
    operation: dict[str, object],
) -> tuple[list[int], int]:
    target_height = int(operation["target_height"])
    resized, left, top = resize_delta_for_target(
        delta_image,
        list(operation["target_center"]),
        target_height,
        width,
        height,
    )
    mask = Image.new("L", (width, height))
    active = resized.point(lambda value: 255 if value else 0)
    mask.paste(active, (left, top))
    halo_radius = int(operation.get("line_gap", 0))
    if halo_radius:
        mask = mask.filter(ImageFilter.MaxFilter(halo_radius * 2 + 1))
    line_pixels = draw_horizontal_lines(
        values,
        width,
        list(operation["lines"]),
        list(mask.get_flattened_data()),
    )
    output_box = compose_delta(
        values,
        width,
        height,
        delta_image,
        list(operation["target_center"]),
        target_height,
        int(operation["target_background"]),
        str(operation["polarity"]),
        int(operation["source_background"]),
    )
    return output_box, line_pixels


def extract_delta(
    values: list[int],
    width: int,
    box: list[int],
    background: int,
    polarity: str,
    min_delta: int,
) -> tuple[Image.Image, list[int]]:
    x0, y0, x1, y1 = box_edges(box)
    delta_values: list[int] = []
    active: list[tuple[int, int]] = []
    for y in range(y0, y1):
        for x in range(x0, x1):
            value = values[y * width + x]
            delta = value - background if polarity == "bright" else background - value
            delta = delta if delta >= min_delta else 0
            delta_values.append(delta)
            if delta:
                active.append((x - x0, y - y0))
    if not active:
        raise ValueError(f"no text pixels found in source box: {box}")
    tight_x0 = min(x for x, _ in active)
    tight_y0 = min(y for _, y in active)
    tight_x1 = max(x for x, _ in active) + 1
    tight_y1 = max(y for _, y in active) + 1
    box_width, box_height = x1 - x0, y1 - y0
    image = Image.new("L", (box_width, box_height))
    image.putdata(delta_values)
    tight = image.crop((tight_x0, tight_y0, tight_x1, tight_y1))
    return tight, [x0 + tight_x0, y0 + tight_y0, tight_x1 - tight_x0, tight_y1 - tight_y0]


def compose_delta(
    values: list[int],
    width: int,
    height: int,
    delta_image: Image.Image,
    target_center: list[float],
    target_height: int,
    target_background: int,
    polarity: str,
    source_background: int,
) -> list[int]:
    resized, left, top = resize_delta_for_target(
        delta_image, target_center, target_height, width, height
    )
    target_width = resized.width
    source_room = 255 - source_background if polarity == "bright" else source_background
    target_room = 255 - target_background if polarity == "bright" else target_background
    if source_room <= 0:
        source_room = 255
    pixels = list(resized.get_flattened_data())
    for local_y in range(target_height):
        for local_x in range(target_width):
            delta = pixels[local_y * target_width + local_x]
            scaled_delta = round(delta * target_room / source_room)
            position = (top + local_y) * width + left + local_x
            if polarity == "bright":
                values[position] = max(values[position], min(255, target_background + scaled_delta))
            else:
                values[position] = min(values[position], max(0, target_background - scaled_delta))
    return [left, top, target_width, target_height]


def apply_resize_crop(
    source: Image.Image, working: Image.Image, operation: dict[str, object]
) -> dict[str, object]:
    source_box = list(operation["source_box"])
    target_box = list(operation["target_box"])
    preserved = [
        (list(box), source.crop(box_edges(list(box))))
        for box in operation.get("preserve_boxes", [])
    ]
    draw = ImageDraw.Draw(working)
    draw.rectangle(box_edges(target_box), fill=int(operation["background"]))
    crop = source.crop(box_edges(source_box)).resize(
        (target_box[2], target_box[3]), Image.Resampling.LANCZOS
    )
    working.paste(crop, (target_box[0], target_box[1]))
    for box, image in preserved:
        working.paste(image, (box[0], box[1]))
    return {"source_box": source_box, "target_box": target_box}


def write_contact_sheet(output_root: Path, rows: list[dict[str, object]]) -> str:
    columns = 3
    cell_width, cell_height = 276, 222
    sheet = Image.new(
        "RGB",
        (cell_width * columns, cell_height * math.ceil(len(rows) / columns)),
        (32, 32, 32),
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


def load_config(path: Path) -> tuple[dict[str, object], list[str]]:
    config = json.loads(path.read_text(encoding="utf-8"))
    chain = [str(path)]
    if "extends" not in config:
        return config, chain
    base_path = path.parent / str(config["extends"])
    base, base_chain = load_config(base_path)
    merged = dict(base)
    merged_canvases = dict(base["canvases"])
    for canvas_id, canvas_overlay in config.get("canvases", {}).items():
        merged_canvas = dict(merged_canvases.get(canvas_id, {}))
        merged_canvas.update(canvas_overlay)
        merged_canvases[canvas_id] = merged_canvas
    merged["canvases"] = merged_canvases
    merged["schema_version"] = config.get("schema_version", base["schema_version"])
    return merged, base_chain + chain


def build(
    translation_dir: Path,
    reference_dir: Path,
    config_path: Path,
    output_root: Path,
) -> dict[str, object]:
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty output root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    config, config_chain = load_config(config_path)
    canvas_configs = config["canvases"]
    translation_paths = {path.stem: path for path in translation_dir.glob("*.png")}
    reference_paths = {path.stem: path for path in reference_dir.glob("*.png")}
    if set(translation_paths) != set(reference_paths):
        raise ValueError("translation/reference image populations differ")
    unknown_config = sorted(set(canvas_configs) - set(translation_paths))
    if unknown_config:
        raise ValueError(f"unknown configured canvases: {unknown_config}")

    rows: list[dict[str, object]] = []
    for canvas_id in sorted(translation_paths):
        translation_path = translation_paths[canvas_id]
        reference_path = reference_paths[canvas_id]
        with Image.open(reference_path) as reference_image:
            if reference_image.mode != "P":
                raise ValueError(f"reference must be P mode: {reference_path}")
            width, height = reference_image.size
            reference_indices = list(reference_image.get_flattened_data())
            reference_levels, reference_greys = reference_rank_greys(reference_indices)
        with Image.open(translation_path) as translated_image:
            source_mode = translated_image.mode
            source_size = translated_image.size
            source_values, scale = downsample_uniform_blocks(
                translated_image, (width, height)
            )
        source_image = Image.new("L", (width, height))
        source_image.putdata(source_values)
        working_values = list(source_values)
        operations: list[dict[str, object]] = []
        canvas_config = canvas_configs.get(canvas_id, {})
        for restore in canvas_config.get("restore_rows", []):
            row_values = fill_rows_from_reference(
                working_values,
                width,
                reference_greys,
                list(restore["box"]),
                list(restore["sample_box"]),
                str(restore.get("method", "max")),
            )
            operations.append(
                {"kind": "restore_rows", "row_values": row_values, **restore}
            )
        for cleanup in canvas_config.get("cleanup", []):
            fill_box(
                working_values,
                width,
                list(cleanup["box"]),
                int(cleanup["background"]),
            )
            operations.append({"kind": "cleanup", **cleanup})
        for block in canvas_config.get("line_aware_text_blocks", []):
            delta, tight_box = extract_delta(
                source_values,
                width,
                list(block["source_box"]),
                int(block["source_background"]),
                str(block["polarity"]),
                int(block["min_delta"]),
            )
            output_box, line_pixels = compose_line_aware_text(
                working_values, width, height, delta, block
            )
            operations.append(
                {
                    "kind": "line_aware_text",
                    "source_tight_box": tight_box,
                    "output_box": output_box,
                    "target_height": block["target_height"],
                    "line_gap": block.get("line_gap", 0),
                    "line_pixel_count": line_pixels,
                }
            )
        for block in canvas_config.get("text_blocks", []):
            delta, tight_box = extract_delta(
                source_values,
                width,
                list(block["source_box"]),
                int(block["source_background"]),
                str(block["polarity"]),
                int(block["min_delta"]),
            )
            output_box = compose_delta(
                working_values,
                width,
                height,
                delta,
                list(block["target_center"]),
                int(block["target_height"]),
                int(block["target_background"]),
                str(block["polarity"]),
                int(block["source_background"]),
            )
            operations.append(
                {
                    "kind": "text_scale",
                    "source_tight_box": tight_box,
                    "output_box": output_box,
                    "target_height": block["target_height"],
                }
            )
        for label in canvas_config.get("font_texts", []):
            output_box = draw_font_text(
                working_values, width, height, label, config_path.parent
            )
            operations.append(
                {
                    "kind": "font_text",
                    "text": label["text"],
                    "font_path": label["font_path"],
                    "font_size": label["font_size"],
                    "output_box": output_box,
                }
            )
        working_image = Image.new("L", (width, height))
        working_image.putdata(working_values)
        for operation in canvas_config.get("resize_crops", []):
            result = apply_resize_crop(source_image, working_image, operation)
            operations.append({"kind": "resize_crop", **result})
        refined_greys = list(working_image.get_flattened_data())
        mapped, errors = map_greys_to_indices(refined_greys, reference_levels)
        for constraint in canvas_config.get("index_constraints", []):
            result = constrain_box_indices(
                mapped,
                errors,
                refined_greys,
                width,
                height,
                list(constraint["box"]),
                reference_levels,
                list(constraint["allowed_indices"]),
            )
            operations.append({"kind": "index_constraint", **result})

        grey_rel = Path("greyscale_1x") / f"{canvas_id}.png"
        grey_path = output_root / grey_rel
        grey_path.parent.mkdir(parents=True, exist_ok=True)
        working_image.save(grey_path, format="PNG", optimize=False)
        indexed_rel = Path("indexed") / f"{canvas_id}.png"
        indexed_path = output_root / indexed_rel
        indexed_path.parent.mkdir(parents=True, exist_ok=True)
        with indexed_image(mapped, width, height) as indexed:
            indexed.save(indexed_path, format="PNG", optimize=False)
        preview_rel = Path("preview_x4") / f"{canvas_id}.png"
        preview_path = output_root / preview_rel
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        with render_index_preview(mapped, width, height) as preview:
            preview.resize((width * 4, height * 4), Image.Resampling.NEAREST).save(
                preview_path, format="PNG", optimize=False
            )
        rows.append(
            {
                "canvas_id": canvas_id,
                "translation_source": str(translation_path),
                "translation_source_sha256": sha256(translation_path.read_bytes()),
                "source_mode": source_mode,
                "source_size": list(source_size),
                "target_size": [width, height],
                "integer_scale": scale,
                "operations": operations,
                "output_index_count": len(set(mapped)),
                "quantized_pixel_count": sum(error != 0 for error in errors),
                "max_greyscale_error": max(errors, default=0),
                "greyscale_png": str(grey_rel),
                "greyscale_png_sha256": sha256(grey_path.read_bytes()),
                "indexed_png": str(indexed_rel),
                "indexed_png_sha256": sha256(indexed_path.read_bytes()),
                "preview_png": str(preview_rel),
                "preview_png_sha256": sha256(preview_path.read_bytes()),
            }
        )
    contact_sheet = write_contact_sheet(output_root, rows)
    result = {
        "schema_version": 1,
        "status": "translated_graphics_background_and_text_scale_refined",
        "translation_dir": str(translation_dir),
        "reference_dir": str(reference_dir),
        "config": str(config_path),
        "config_chain": config_chain,
        "image_count": len(rows),
        "configured_canvas_count": len(canvas_configs),
        "contact_sheet": contact_sheet,
        "outputs": rows,
        "not_proved": [
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
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = build(
        args.translation_dir, args.reference_dir, args.config, args.output_root
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "image_count": result["image_count"],
                "configured_canvas_count": result["configured_canvas_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
