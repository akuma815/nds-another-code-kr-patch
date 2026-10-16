#!/usr/bin/env python3
"""Build reversible, wrap-normalized indexed PNG canvases for graphics editing."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import struct
from pathlib import Path

from PIL import Image

from extract_pack_graphics import (
    arrange_indices,
    classify_indexed_bitmap,
    indexed_bitmap_offsets,
    resolve_layout,
    unpack_indices,
)
from render_pack_index_previews import render_index_preview


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def roll_x(values: list[int], width: int, height: int, shift: int) -> list[int]:
    if not width:
        raise ValueError("width must be positive")
    shift %= width
    if not shift:
        return list(values)
    result: list[int] = []
    for y in range(height):
        row = values[y * width : (y + 1) * width]
        result.extend(row[-shift:] + row[:-shift])
    return result


def roll_tile_sequence(values: list[int], shift_tiles: int) -> list[int]:
    if len(values) % 64:
        raise ValueError("tiled8 values must contain whole 8x8 tiles")
    tiles = [values[offset : offset + 64] for offset in range(0, len(values), 64)]
    shift_tiles %= len(tiles)
    if shift_tiles:
        tiles = tiles[-shift_tiles:] + tiles[:-shift_tiles]
    return [value for tile in tiles for value in tile]


def unarrange_indices(values: list[int], width: int, height: int, layout: str) -> list[int]:
    if layout == "linear":
        return list(values)
    if layout != "tiled8" or width % 8 or height % 8:
        raise ValueError(f"unsupported inverse layout: {layout}")
    result: list[int] = []
    for tile_y in range(0, height, 8):
        for tile_x in range(0, width, 8):
            for local_y in range(8):
                start = (tile_y + local_y) * width + tile_x
                result.extend(values[start : start + 8])
    return result


def pack_indices(values: list[int], bpp: int) -> bytes:
    if bpp == 8:
        return bytes(values)
    if bpp != 4 or len(values) % 2:
        raise ValueError("4bpp values require an even pixel count")
    output = bytearray()
    for offset in range(0, len(values), 2):
        lo, hi = values[offset], values[offset + 1]
        if not 0 <= lo < 16 or not 0 <= hi < 16:
            raise ValueError("4bpp index exceeds 0..15")
        output.append(lo | (hi << 4))
    return bytes(output)


def indexed_image(values: list[int], width: int, height: int) -> Image.Image:
    image = Image.new("P", (width, height))
    image.putpalette([channel for value in range(256) for channel in (value, value, value)])
    image.putdata(values)
    return image


def load_config(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def build(pack_root: Path, config_path: Path, output_root: Path) -> dict[str, object]:
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty output root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    pack_report = json.loads((pack_root / "report.json").read_text(encoding="utf-8"))
    resources = {str(row["resource_id"]): row for row in pack_report["resources"]}
    outputs: list[dict[str, object]] = []
    for config in load_config(config_path):
        resource_id = config["resource_id"]
        if resource_id not in resources:
            raise ValueError(f"resource not found: {resource_id}")
        resource = resources[resource_id]
        if resource.get("format") not in ("indexed4_bgr555", "indexed8_bgr555"):
            raise ValueError(f"resource is not an indexed bitmap: {resource_id}")
        data = (pack_root / str(resource["decoded_path"])).read_bytes()
        info = classify_indexed_bitmap(data)
        if info is None:
            raise ValueError(f"resource is not a recognized indexed bitmap: {resource_id}")
        width, height = int(info["width"]), int(info["height"])
        pixel_size = int(info["pixel_size"])
        pixel_offset, _palette_offset = indexed_bitmap_offsets(data, info)
        bpp = int(resource["bpp"])
        source_pixels = data[pixel_offset : pixel_offset + pixel_size]
        storage_indices = unpack_indices(source_pixels, bpp, width * height)
        layout = resolve_layout(str(resource.get("selected_layout") or "auto"), width, height)
        shift_x = int(config["shift_x"])
        shift_mode = config.get("shift_mode", "raster_rows") or "raster_rows"
        if shift_mode == "tiled8_sequence":
            if layout != "tiled8" or shift_x % 8:
                raise ValueError(
                    f"tiled8_sequence requires tiled8 and an 8px shift: {config['canvas_id']}"
                )
            normalized_storage = roll_tile_sequence(storage_indices, shift_x // 8)
            normalized = arrange_indices(normalized_storage, width, height, layout)
        elif shift_mode == "raster_rows":
            raster = arrange_indices(storage_indices, width, height, layout)
            normalized = roll_x(raster, width, height, shift_x)
        else:
            raise ValueError(f"unsupported shift mode: {shift_mode}")
        crop_x, crop_y = int(config["crop_x"]), int(config["crop_y"])
        crop_w, crop_h = int(config["crop_w"]), int(config["crop_h"])
        if crop_x < 0 or crop_y < 0 or crop_x + crop_w > width or crop_y + crop_h > height:
            raise ValueError(f"crop exceeds resource bounds: {config['canvas_id']}")
        crop_values = [
            normalized[y * width + crop_x : y * width + crop_x + crop_w]
            for y in range(crop_y, crop_y + crop_h)
        ]
        flat_crop = [value for row in crop_values for value in row]
        editable_rel = Path("editable_indexed") / f"{config['canvas_id']}.png"
        editable_path = output_root / editable_rel
        editable_path.parent.mkdir(parents=True, exist_ok=True)
        with indexed_image(flat_crop, crop_w, crop_h) as image:
            image.save(editable_path, format="PNG", optimize=False)
        preview_rel = Path("preview_x4") / f"{config['canvas_id']}.png"
        preview_path = output_root / preview_rel
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        with render_index_preview(flat_crop, crop_w, crop_h) as preview:
            preview.resize((crop_w * 4, crop_h * 4), Image.Resampling.NEAREST).save(
                preview_path, format="PNG", optimize=False
            )

        # Prove that export + unchanged import + inverse transform is byte-identical.
        with Image.open(editable_path) as exported:
            if exported.mode != "P" or exported.size != (crop_w, crop_h):
                raise ValueError(f"editable PNG contract changed: {editable_path}")
            imported = list(exported.get_flattened_data())
        rebuilt_normalized = list(normalized)
        cursor = 0
        for y in range(crop_y, crop_y + crop_h):
            start = y * width + crop_x
            rebuilt_normalized[start : start + crop_w] = imported[cursor : cursor + crop_w]
            cursor += crop_w
        if shift_mode == "tiled8_sequence":
            rebuilt_normalized_storage = unarrange_indices(
                rebuilt_normalized, width, height, layout
            )
            rebuilt_storage = roll_tile_sequence(
                rebuilt_normalized_storage, -(shift_x // 8)
            )
        else:
            rebuilt_raster = roll_x(rebuilt_normalized, width, height, -shift_x)
            rebuilt_storage = unarrange_indices(rebuilt_raster, width, height, layout)
        rebuilt_pixels = pack_indices(rebuilt_storage, bpp)
        if rebuilt_pixels != source_pixels:
            raise ValueError(f"no-change roundtrip mismatch: {resource_id}")
        outputs.append(
            {
                **config,
                "width": width,
                "height": height,
                "bpp": bpp,
                "layout": layout,
                "shift_mode": shift_mode,
                "source_pixel_sha256": sha256(source_pixels),
                "editable_png": str(editable_rel),
                "editable_png_sha256": sha256(editable_path.read_bytes()),
                "preview_png": str(preview_rel),
                "preview_png_sha256": sha256(preview_path.read_bytes()),
                "inverse_shift_x": -shift_x,
                "no_change_roundtrip": "pass_byte_identical_pixel_buffer",
            }
        )
    result = {
        "schema_version": 1,
        "status": "reversible_wrap_normalized_edit_canvases_ready",
        "pack_root": str(pack_root),
        "config": str(config_path),
        "canvas_count": len(outputs),
        "roundtrip_pass_count": sum(
            row["no_change_roundtrip"] == "pass_byte_identical_pixel_buffer"
            for row in outputs
        ),
        "outputs": outputs,
        "not_proved": [
            "runtime absolute position for inferred transforms",
            "runtime palette colours in greyscale edit previews",
            "edited PNG import and pack reinsertion",
            "emulator playback after a graphics edit",
        ],
    }
    (output_root / "report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.pack_root, args.config, args.output_root)
    print(
        json.dumps(
            {
                "status": result["status"],
                "canvas_count": result["canvas_count"],
                "roundtrip_pass_count": result["roundtrip_pass_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
