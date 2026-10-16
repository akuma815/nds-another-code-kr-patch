#!/usr/bin/env python3
"""Extract decoded Another Code pack resources and render indexed bitmaps."""

from __future__ import annotations

import argparse
import csv
import json
import re
import struct
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageDraw

from analyze_pack_resources import (
    classify_wrapper,
    decoded_payload,
    parse_directory,
    sha256,
)


SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_name(value: str) -> str:
    result = SAFE_NAME.sub("_", value).strip("._")
    if not result:
        raise ValueError(f"resource name has no safe filename representation: {value!r}")
    return result


def classify_indexed_bitmap(data: bytes) -> dict[str, int | str] | None:
    """Recognize the exact 16-byte indexed-bitmap envelope used by pack assets."""
    if len(data) < 16:
        return None
    flags = data[:4].hex().upper()
    width, height = struct.unpack_from("<HH", data, 4)
    pixel_size, palette_size = struct.unpack_from("<II", data, 8)
    if not width or not height or width > 4096 or height > 4096:
        return None
    if palette_size not in (0x20, 0x200):
        return None
    if 16 + pixel_size + palette_size != len(data):
        return None
    pixel_count = width * height
    if pixel_size == pixel_count and palette_size == 0x200:
        bpp = 8
    elif pixel_size * 2 == pixel_count and palette_size == 0x20:
        bpp = 4
    else:
        return None
    return {
        "format": f"indexed{bpp}_bgr555",
        "flags_hex": flags,
        "width": width,
        "height": height,
        "bpp": bpp,
        "pixel_size": pixel_size,
        "palette_size": palette_size,
    }


def indexed_bitmap_offsets(data: bytes, info: dict[str, int | str]) -> tuple[int, int]:
    """Return pixel and palette offsets for both observed bitmap envelope orders."""
    pixel_size = int(info["pixel_size"])
    palette_size = int(info["palette_size"])
    flags_hex = str(info["flags_hex"])
    if flags_hex == "00001000":
        palette_offset = 16
        pixel_offset = palette_offset + palette_size
    else:
        pixel_offset = 16
        palette_offset = pixel_offset + pixel_size
    if pixel_offset + pixel_size > len(data) or palette_offset + palette_size > len(data):
        raise ValueError("indexed bitmap payload order exceeds decoded resource")
    return pixel_offset, palette_offset


def unpack_indices(pixel_data: bytes, bpp: int, pixel_count: int) -> list[int]:
    if bpp == 8:
        if len(pixel_data) != pixel_count:
            raise ValueError("8bpp pixel length mismatch")
        return list(pixel_data)
    if bpp != 4 or len(pixel_data) * 2 != pixel_count:
        raise ValueError("4bpp pixel length mismatch")
    result: list[int] = []
    for value in pixel_data:
        result.extend((value & 0x0F, value >> 4))
    return result


def arrange_indices(indices: list[int], width: int, height: int, layout: str) -> list[int]:
    if len(indices) != width * height:
        raise ValueError("index count does not match dimensions")
    if layout == "linear":
        return indices
    if layout != "tiled8":
        raise ValueError(f"unsupported pixel layout: {layout}")
    if width % 8 or height % 8:
        raise ValueError("8x8 tiled layout requires dimensions divisible by 8")
    result = [0] * len(indices)
    source = 0
    for tile_y in range(0, height, 8):
        for tile_x in range(0, width, 8):
            for local_y in range(8):
                target = (tile_y + local_y) * width + tile_x
                result[target : target + 8] = indices[source : source + 8]
                source += 8
    return result


def resolve_layout(layout: str, width: int, height: int) -> str:
    """Select the observed NDS 8x8 tile order, with a safe linear fallback."""
    if layout != "auto":
        return layout
    return "tiled8" if width % 8 == 0 and height % 8 == 0 else "linear"


def decode_bgr555(palette_data: bytes) -> list[tuple[int, int, int, int]]:
    if len(palette_data) % 2:
        raise ValueError("BGR555 palette length is odd")
    result = []
    for offset in range(0, len(palette_data), 2):
        value = struct.unpack_from("<H", palette_data, offset)[0]
        red = (value & 0x1F) * 255 // 31
        green = ((value >> 5) & 0x1F) * 255 // 31
        blue = ((value >> 10) & 0x1F) * 255 // 31
        result.append((red, green, blue, 255))
    return result


def render_indexed_bitmap(data: bytes, info: dict[str, int | str], layout: str) -> Image.Image:
    width = int(info["width"])
    height = int(info["height"])
    pixel_size = int(info["pixel_size"])
    palette_size = int(info["palette_size"])
    pixel_offset, palette_offset = indexed_bitmap_offsets(data, info)
    pixel_data = data[pixel_offset : pixel_offset + pixel_size]
    palette_data = data[palette_offset : palette_offset + palette_size]
    indices = unpack_indices(pixel_data, int(info["bpp"]), width * height)
    indices = arrange_indices(indices, width, height, resolve_layout(layout, width, height))
    palette = decode_bgr555(palette_data)
    if max(indices, default=0) >= len(palette):
        raise ValueError("pixel index exceeds palette")
    image = Image.new("RGBA", (width, height))
    image.putdata([palette[index] for index in indices])
    return image


def parse_head_texture_records(data: bytes) -> list[dict[str, int | str]]:
    """Parse the fixed 64-byte TEX records in a HEAD model resource."""
    if len(data) < 0xA8 or data[:4] != b"HEAD":
        return []
    texture_count = struct.unpack_from("<H", data, 0xA6)[0]
    if not texture_count:
        return []
    tex_offset = data.find(b"TEX\0")
    if tex_offset < 0:
        return []
    records = []
    for index in range(texture_count):
        offset = tex_offset + 32 + index * 64
        if offset + 64 > len(data):
            raise ValueError("HEAD TEX table exceeds model resource")
        name = data[offset : offset + 16].split(b"\0", 1)[0].decode("ascii", "replace")
        format_code = struct.unpack_from("<I", data, offset + 20)[0]
        width, height = struct.unpack_from("<HH", data, offset + 24)
        pixel_size = struct.unpack_from("<I", data, offset + 32)[0]
        palette_size = struct.unpack_from("<I", data, offset + 40)[0]
        pixel_count = width * height
        if pixel_size == pixel_count and palette_size == 0x200:
            bpp = 8
        elif pixel_size * 2 == pixel_count and palette_size == 0x20:
            bpp = 4
        else:
            raise ValueError(
                f"unsupported HEAD texture contract: {name} {width}x{height} "
                f"pixels={pixel_size} palette={palette_size}"
            )
        records.append(
            {
                "texture_index": index,
                "texture_name": name or f"texture_{index:03d}",
                "format_code": format_code,
                "width": width,
                "height": height,
                "bpp": bpp,
                "pixel_size": pixel_size,
                "palette_size": palette_size,
            }
        )
    return records


def extract_model_textures(output_root: Path, rows: list[dict]) -> dict[str, object]:
    """Split exact HEAD + *_tex pairs into individual linear 3D textures."""
    by_id = {str(row["resource_id"]): row for row in rows}
    texture_rows: list[dict[str, object]] = []
    pairs: list[dict[str, object]] = []
    consumed_ids: set[str] = set()
    for model_row in rows:
        model_path = output_root / str(model_row["decoded_path"])
        model_data = model_path.read_bytes()
        if model_data[:4] != b"HEAD":
            continue
        records = parse_head_texture_records(model_data)
        if not records:
            continue
        companion_id = f"{model_row['resource_id']}_tex"
        companion_row = by_id.get(companion_id)
        if companion_row is None:
            continue
        companion_path = output_root / str(companion_row["decoded_path"])
        companion_data = companion_path.read_bytes()
        expected_size = sum(
            int(record["pixel_size"]) + int(record["palette_size"]) for record in records
        )
        if expected_size != len(companion_data):
            raise ValueError(
                f"HEAD texture payload mismatch for {model_row['resource_id']}: "
                f"expected {expected_size}, got {len(companion_data)}"
            )
        cursor = 0
        archive_dir = safe_name(str(model_row["archive"]))
        model_dir = safe_name(str(model_row["resource"]))
        for record in records:
            pixel_size = int(record["pixel_size"])
            palette_size = int(record["palette_size"])
            pixel_data = companion_data[cursor : cursor + pixel_size]
            palette_data = companion_data[cursor + pixel_size : cursor + pixel_size + palette_size]
            cursor += pixel_size + palette_size
            envelope = (
                b"\0\0\0\0"
                + struct.pack(
                    "<HHII",
                    int(record["width"]),
                    int(record["height"]),
                    pixel_size,
                    palette_size,
                )
                + pixel_data
                + palette_data
            )
            info = classify_indexed_bitmap(envelope)
            if info is None:
                raise ValueError("internal model texture classification failure")
            texture_name = safe_name(str(record["texture_name"]))
            png_rel = (
                Path("model_textures")
                / archive_dir
                / model_dir
                / f"{int(record['texture_index']):03d}_{texture_name}.png"
            )
            png_path = output_root / png_rel
            png_path.parent.mkdir(parents=True, exist_ok=True)
            with render_indexed_bitmap(envelope, info, "linear") as image:
                image.save(png_path, format="PNG", optimize=False)
            texture_rows.append(
                {
                    "archive": model_row["archive"],
                    "model_resource_id": model_row["resource_id"],
                    "payload_resource_id": companion_id,
                    "texture_index": record["texture_index"],
                    "resource": f"{model_row['resource']}/{record['texture_name']}",
                    "texture_name": record["texture_name"],
                    "format_code": record["format_code"],
                    "width": record["width"],
                    "height": record["height"],
                    "bpp": record["bpp"],
                    "pixel_size": pixel_size,
                    "palette_size": palette_size,
                    "payload_offset": cursor - pixel_size - palette_size,
                    "primary_png_path": str(png_rel),
                    "selected_layout": "linear",
                    "png_sha256": sha256(png_path.read_bytes()),
                }
            )
        consumed_ids.update((str(model_row["resource_id"]), companion_id))
        model_row["secondary_classification"] = "head_model_texture_metadata"
        companion_row["secondary_classification"] = "head_model_texture_payload"
        pairs.append(
            {
                "model_resource_id": model_row["resource_id"],
                "payload_resource_id": companion_id,
                "texture_count": len(records),
                "payload_size": len(companion_data),
                "size_contract": "pass_exact",
            }
        )

    fields = (
        "archive",
        "model_resource_id",
        "payload_resource_id",
        "texture_index",
        "texture_name",
        "format_code",
        "width",
        "height",
        "bpp",
        "pixel_size",
        "palette_size",
        "payload_offset",
        "primary_png_path",
        "selected_layout",
        "png_sha256",
    )
    catalog_path = output_root / "model_texture_catalog.csv"
    with catalog_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(texture_rows)
    sheets = write_contact_sheets(output_root, texture_rows, "model_texture_contact_sheets")
    return {
        "pair_count": len(pairs),
        "texture_count": len(texture_rows),
        "consumed_resource_count": len(consumed_ids),
        "remaining_unclassified_resource_count": sum(
            row["format"] == "unknown" and row["resource_id"] not in consumed_ids for row in rows
        ),
        "catalog": {
            "path": str(catalog_path.relative_to(output_root)),
            "sha256": sha256(catalog_path.read_bytes()),
        },
        "contact_sheets": sheets,
        "pairs": pairs,
    }


def write_contact_sheets(
    output_root: Path, rows: list[dict], folder: str = "contact_sheets"
) -> list[dict[str, object]]:
    """Write one labeled review sheet per archive using each row's primary PNG."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("primary_png_path"):
            grouped[str(row["archive"])].append(row)

    result: list[dict[str, object]] = []
    columns = 5
    cell_width, cell_height = 176, 154
    thumb_width, thumb_height = 160, 120
    for archive, archive_rows in sorted(grouped.items()):
        row_count = (len(archive_rows) + columns - 1) // columns
        sheet = Image.new("RGB", (columns * cell_width, row_count * cell_height), (32, 32, 32))
        draw = ImageDraw.Draw(sheet)
        for index, row in enumerate(archive_rows):
            x = (index % columns) * cell_width
            y = (index // columns) * cell_height
            png_path = output_root / str(row["primary_png_path"])
            with Image.open(png_path) as source:
                thumb = source.convert("RGB")
                thumb.thumbnail((thumb_width, thumb_height), Image.Resampling.NEAREST)
            paste_x = x + (cell_width - thumb.width) // 2
            paste_y = y + 4 + (thumb_height - thumb.height) // 2
            sheet.paste(thumb, (paste_x, paste_y))
            draw.text((x + 5, y + 128), str(row["resource"]), fill=(255, 255, 255))
        sheet_rel = Path(folder) / f"{safe_name(archive)}.png"
        sheet_path = output_root / sheet_rel
        sheet_path.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(sheet_path, format="PNG", optimize=False)
        result.append(
            {
                "archive": archive,
                "resource_count": len(archive_rows),
                "path": str(sheet_rel),
                "sha256": sha256(sheet_path.read_bytes()),
            }
        )
    return result


def extract(args: argparse.Namespace) -> dict:
    pack_dir = args.pack_dir
    output_root = args.output_root
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty extraction root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    decoded_root = output_root / "decoded"
    png_root = output_root / "png"

    selectors = set(args.select or [])
    layouts = args.layouts.split(",")
    if any(layout not in ("auto", "linear", "tiled8") for layout in layouts):
        raise ValueError("layouts must contain only auto, linear and tiled8")

    head_inf = (pack_dir / "head.inf").read_bytes()
    head_bin = (pack_dir / "head.bin").read_bytes()
    archives, _ = parse_directory(head_inf)
    rows: list[dict] = []
    format_counts: Counter[str] = Counter()
    wrapper_counts: Counter[str] = Counter()
    selected_seen: set[str] = set()

    for archive in archives:
        archive_data = (pack_dir / f"{archive.name}.bin").read_bytes()
        resources, _ = parse_directory(
            head_bin[archive.offset : archive.offset + archive.size],
            base_offset=archive.offset,
        )
        for resource in resources:
            resource_id = f"{archive.name}/{resource.name}"
            if selectors and resource_id not in selectors:
                continue
            selected_seen.add(resource_id)
            body = archive_data[resource.offset : resource.offset + resource.size]
            wrapper = classify_wrapper(body)
            decoded = decoded_payload(body, wrapper)
            wrapper_counts[str(wrapper["kind"])] += 1
            archive_dir = safe_name(archive.name)
            resource_filename = safe_name(resource.name)
            decoded_rel = Path("decoded") / archive_dir / f"{resource_filename}.bin"
            decoded_path = output_root / decoded_rel
            decoded_path.parent.mkdir(parents=True, exist_ok=True)
            decoded_path.write_bytes(decoded)

            bitmap = classify_indexed_bitmap(decoded)
            row = {
                "resource_id": resource_id,
                "archive": archive.name,
                "resource": resource.name,
                "archive_offset": resource.offset,
                "stored_size": len(body),
                "stored_sha256": sha256(body),
                "wrapper_kind": wrapper["kind"],
                "decoded_size": len(decoded),
                "decoded_sha256": sha256(decoded),
                "decoded_path": str(decoded_rel),
                "format": "unknown",
                "flags_hex": "",
                "width": "",
                "height": "",
                "bpp": "",
                "palette_size": "",
                "png_paths": [],
                "primary_png_path": "",
                "selected_layout": "",
                "secondary_classification": "",
                "state": "decoded_unclassified",
            }
            if bitmap is not None:
                row.update(
                    {
                        "format": bitmap["format"],
                        "width": bitmap["width"],
                        "height": bitmap["height"],
                        "bpp": bitmap["bpp"],
                        "palette_size": bitmap["palette_size"],
                        "flags_hex": bitmap["flags_hex"],
                        "state": "rendered_tiled8_representative_visual_confirmation",
                    }
                )
                for layout in layouts:
                    selected_layout = resolve_layout(
                        layout, int(bitmap["width"]), int(bitmap["height"])
                    )
                    if selected_layout == "tiled8" and (
                        int(bitmap["width"]) % 8 or int(bitmap["height"]) % 8
                    ):
                        continue
                    suffix = "" if len(layouts) == 1 else f"_{layout}"
                    png_rel = Path("png") / archive_dir / f"{resource_filename}{suffix}.png"
                    png_path = output_root / png_rel
                    png_path.parent.mkdir(parents=True, exist_ok=True)
                    with render_indexed_bitmap(decoded, bitmap, layout) as image:
                        image.save(png_path, format="PNG", optimize=False)
                    row["png_paths"].append(str(png_rel))
                    if not row["primary_png_path"]:
                        row["primary_png_path"] = str(png_rel)
                        row["selected_layout"] = selected_layout
                format_counts[str(bitmap["format"])] += 1
            else:
                format_counts["unknown"] += 1
            rows.append(row)

    missing = sorted(selectors - selected_seen)
    if missing:
        raise ValueError(f"selected resources not found: {missing}")

    model_textures = extract_model_textures(output_root, rows)

    catalog_fields = (
        "resource_id",
        "archive",
        "resource",
        "archive_offset",
        "stored_size",
        "stored_sha256",
        "wrapper_kind",
        "decoded_size",
        "decoded_sha256",
        "decoded_path",
        "format",
        "flags_hex",
        "width",
        "height",
        "bpp",
        "palette_size",
        "png_paths",
        "primary_png_path",
        "selected_layout",
        "secondary_classification",
        "state",
    )
    catalog_path = output_root / "catalog.csv"
    with catalog_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=catalog_fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            csv_row = dict(row)
            csv_row["png_paths"] = ";".join(row["png_paths"])
            writer.writerow(csv_row)

    contact_sheets = write_contact_sheets(output_root, rows)

    report = {
        "schema_version": 1,
        "status": "decoded_resources_extracted_indexed_bitmaps_rendered_tiled8_representative_visual_confirmation",
        "inputs": {
            "pack_dir": str(pack_dir),
            "head_inf_sha256": sha256(head_inf),
            "head_bin_sha256": sha256(head_bin),
        },
        "output_root": str(output_root),
        "layouts": layouts,
        "selected_resources": sorted(selectors),
        "resource_count": len(rows),
        "wrapper_counts": dict(sorted(wrapper_counts.items())),
        "format_counts": dict(sorted(format_counts.items())),
        "decoded_file_count": len(rows),
        "png_file_count": sum(len(row["png_paths"]) for row in rows),
        "contact_sheet_count": len(contact_sheets),
        "contact_sheets": contact_sheets,
        "model_textures": model_textures,
        "catalog": {
            "path": str(catalog_path.relative_to(output_root)),
            "sha256": sha256(catalog_path.read_bytes()),
        },
        "gates": {
            "pack_directory_and_decompression": "pass",
            "indexed_bitmap_envelope": "pass_exact_size_and_palette_contract",
            "pixel_layout": "pass_representative_8bpp_tiled8_visual_confirmation",
            "four_bpp_semantic_layout": "pending_per_resource_review",
            "head_model_texture_pairs": "pass_exact_payload_size_for_all_parsed_pairs",
            "head_model_texture_layout": "pass_linear_3d_texture_extraction",
            "unknown_resource_formats": "pending",
            "no_change_roundtrip": "not_implemented_extraction_only",
        },
        "not_proved": [
            "whole-population semantic confirmation of every rendered image",
            "whether every 4bpp image is visual art rather than a mask or work buffer",
            "whether every unknown resource contains no editable graphics",
            "re-encoding and pack reinsertion compatibility",
            "runtime consumption of any future edited image",
        ],
        "resources": rows,
    }
    report_path = output_root / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--layouts", default="auto")
    parser.add_argument("--select", action="append")
    args = parser.parse_args()
    extract(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
