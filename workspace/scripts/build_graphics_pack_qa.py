#!/usr/bin/env python3
"""Import translated indexed canvases and build an in-place graphics QA ROM."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from analyze_pack_resources import (
    WRAPPER_PREFIX,
    classify_wrapper,
    compress_type1,
    decoded_payload,
    parse_directory,
    validate_nonoverlap,
)
from build_graphics_edit_canvases import (
    pack_indices,
    roll_tile_sequence,
    roll_x,
    unarrange_indices,
)
from extract_pack_graphics import (
    arrange_indices,
    classify_indexed_bitmap,
    indexed_bitmap_offsets,
    resolve_layout,
    unpack_indices,
)
from nds_inventory import NdsRom, diff_intervals


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def align(value: int, boundary: int) -> int:
    return (value + boundary - 1) & ~(boundary - 1)


def nds_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc


@dataclass(frozen=True)
class Write:
    owner: str
    meaning: str
    rom_offset: int
    expected: bytes
    replacement: bytes


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def file_id_for(rom: NdsRom, path: str) -> int:
    matches = [file_id for file_id, candidate in rom.paths.items() if candidate == path]
    if len(matches) != 1:
        raise ValueError(f"expected one NitroFS file for {path}, found {len(matches)}")
    return matches[0]


def file_bytes(rom: NdsRom, path: str) -> tuple[int, int, bytes]:
    file_id = file_id_for(rom, path)
    start, end = rom.fat[file_id]
    return start, end, rom.data[start:end]


def import_canvas(
    decoded: bytes,
    translated_path: Path,
    config: dict[str, str],
    catalog: dict[str, str],
    allowed_indices: set[int] | None = None,
) -> tuple[bytes, dict[str, object]]:
    info = classify_indexed_bitmap(decoded)
    if info is None:
        raise ValueError(f"target is not an indexed bitmap: {catalog['resource_id']}")
    width, height = int(info["width"]), int(info["height"])
    if (width, height, int(info["bpp"])) != (
        int(catalog["width"]),
        int(catalog["height"]),
        int(catalog["bpp"]),
    ):
        raise ValueError(f"catalog bitmap contract mismatch: {catalog['resource_id']}")
    pixel_size = int(info["pixel_size"])
    pixel_offset, palette_offset = indexed_bitmap_offsets(decoded, info)
    palette_size = int(info["palette_size"])
    storage = unpack_indices(
        decoded[pixel_offset : pixel_offset + pixel_size], int(info["bpp"]), width * height
    )
    layout = resolve_layout(catalog["selected_layout"], width, height)
    shift_x = int(config["shift_x"])
    shift_mode = config["shift_mode"]
    if shift_mode == "tiled8_sequence":
        if layout != "tiled8" or shift_x % 8:
            raise ValueError(f"invalid tiled8 transform: {config['canvas_id']}")
        normalized_storage = roll_tile_sequence(storage, shift_x // 8)
        normalized = arrange_indices(normalized_storage, width, height, layout)
    elif shift_mode == "raster_rows":
        normalized = roll_x(arrange_indices(storage, width, height, layout), width, height, shift_x)
    else:
        raise ValueError(f"unsupported shift mode: {shift_mode}")

    crop_x, crop_y = int(config["crop_x"]), int(config["crop_y"])
    crop_w, crop_h = int(config["crop_w"]), int(config["crop_h"])
    with Image.open(translated_path) as translated:
        if translated.mode != "P" or translated.size != (crop_w, crop_h):
            raise ValueError(f"translated PNG contract mismatch: {translated_path}")
        imported = list(translated.get_flattened_data())
    if allowed_indices is None:
        allowed_indices = set(storage)
    if not set(imported) <= allowed_indices:
        raise ValueError(f"translated PNG uses foreign palette indices: {translated_path}")
    cursor = 0
    for y in range(crop_y, crop_y + crop_h):
        start = y * width + crop_x
        normalized[start : start + crop_w] = imported[cursor : cursor + crop_w]
        cursor += crop_w

    if shift_mode == "tiled8_sequence":
        rebuilt_normalized_storage = unarrange_indices(normalized, width, height, layout)
        rebuilt_storage = roll_tile_sequence(rebuilt_normalized_storage, -(shift_x // 8))
    else:
        rebuilt_raster = roll_x(normalized, width, height, -shift_x)
        rebuilt_storage = unarrange_indices(rebuilt_raster, width, height, layout)
    rebuilt_pixels = pack_indices(rebuilt_storage, int(info["bpp"]))
    output_bytes = bytearray(decoded)
    output_bytes[pixel_offset : pixel_offset + pixel_size] = rebuilt_pixels
    output = bytes(output_bytes)
    changed, intervals = diff_intervals(decoded, output)
    if not changed:
        raise ValueError(f"translated canvas makes no decoded change: {config['canvas_id']}")
    if any(
        row["start"] < pixel_offset or row["end"] > pixel_offset + pixel_size
        for row in intervals
    ):
        raise ValueError(f"translated canvas changed protected bytes: {config['canvas_id']}")
    if output[palette_offset : palette_offset + palette_size] != decoded[
        palette_offset : palette_offset + palette_size
    ]:
        raise ValueError(f"translated canvas changed palette bytes: {config['canvas_id']}")
    return output, {
        "decoded_changed_byte_count": changed,
        "decoded_changed_intervals": intervals,
        "payload_order": "palette_first" if pixel_offset > 16 else "pixels_first",
        "pixel_range": [pixel_offset, pixel_offset + pixel_size],
        "palette_range": [palette_offset, palette_offset + palette_size],
        "palette_preserved": True,
        "inverse_shift_x": -shift_x,
    }


def compressed_wrapper(decoded: bytes) -> bytes:
    payload = compress_type1(decoded)
    wrapper = WRAPPER_PREFIX + b"\x01" + struct.pack("<III", len(decoded), len(payload), 0) + payload
    classified = classify_wrapper(wrapper)
    if classified["kind"] != "compressed" or not classified["payload_size_matches_entry"]:
        raise ValueError("internal compressed wrapper validation failed")
    if decoded_payload(wrapper, classified) != decoded:
        raise ValueError("type-1 compressor self-roundtrip failed")
    return wrapper


def validate_writes(writes: list[Write], source: bytes) -> None:
    previous_end = -1
    for write in sorted(writes, key=lambda row: row.rom_offset):
        end = write.rom_offset + len(write.replacement)
        if write.rom_offset < previous_end:
            raise ValueError(f"overlapping Expected Writes at 0x{write.rom_offset:X}")
        if source[write.rom_offset : write.rom_offset + len(write.expected)] != write.expected:
            raise ValueError(f"Expected Write source mismatch: {write.owner}")
        if len(write.expected) != len(write.replacement):
            raise ValueError(f"in-place Expected Write changed length: {write.owner}")
        previous_end = end


def build(args: argparse.Namespace) -> dict[str, object]:
    if args.output_rom.exists() or args.report.exists():
        raise FileExistsError("refusing to overwrite an existing output or report")
    source_config_bytes = args.source_config.read_bytes()
    source_config = json.loads(source_config_bytes.decode("utf-8"))
    source = NdsRom(args.source_rom)
    expected_source = source_config["source"]
    if len(source.data) != int(expected_source["size"]) or sha256(source.data) != expected_source["sha256"]:
        raise ValueError("source ROM identity mismatch")
    base = NdsRom(args.base_rom)
    base_report_bytes = args.base_report.read_bytes()
    base_report = json.loads(base_report_bytes.decode("utf-8"))
    if len(base.data) != len(source.data) or sha256(base.data) != base_report["output"]["sha256"]:
        raise ValueError("base translation ROM does not match its report")
    if source.header != base.header or source.fat != base.fat or source.paths != base.paths:
        raise ValueError("base translation ROM changed the NDS filesystem contract")

    pack_report_bytes = (args.pack_root / "report.json").read_bytes()
    pack_report = json.loads(pack_report_bytes.decode("utf-8"))
    catalog_rows = load_csv(args.pack_root / "catalog.csv")
    catalog = {row["resource_id"]: row for row in catalog_rows}
    configs = load_csv(args.canvas_config)
    translated_paths = {path.stem: path for path in args.translated_dir.glob("*.png")}
    if set(translated_paths) != {row["canvas_id"] for row in configs}:
        raise ValueError("translated PNG and canvas-config populations differ")
    resource_ids = [row["resource_id"] for row in configs]
    if any(resource_id not in catalog for resource_id in resource_ids):
        raise ValueError("canvas config names a resource absent from the pack catalog")

    head_inf_start, head_inf_end, head_inf = file_bytes(source, "data/pack/head.inf")
    head_bin_start, head_bin_end, head_bin = file_bytes(source, "data/pack/head.bin")
    if sha256(head_inf) != pack_report["inputs"]["head_inf_sha256"]:
        raise ValueError("source head.inf differs from graphics extraction baseline")
    if sha256(head_bin) != pack_report["inputs"]["head_bin_sha256"]:
        raise ValueError("source head.bin differs from graphics extraction baseline")
    if base.data[head_inf_start:head_inf_end] != head_inf or base.data[head_bin_start:head_bin_end] != head_bin:
        raise ValueError("base translation ROM unexpectedly changed pack directories")

    archives, consumed = parse_directory(head_inf)
    if consumed != len(head_inf):
        raise ValueError("head.inf has trailing bytes")
    validate_nonoverlap(archives, len(head_bin))
    archive_by_name = {row.name: row for row in archives}
    source_archives: dict[str, tuple[int, int, bytes]] = {}
    resource_records: dict[str, object] = {}
    archive_resources: dict[str, list[object]] = {}
    for archive in archives:
        start, end, data = file_bytes(source, f"data/pack/{archive.name}.bin")
        if base.data[start:end] != data:
            raise ValueError(f"base translation ROM unexpectedly changed pack archive: {archive.name}")
        records, descriptor_consumed = parse_directory(
            head_bin[archive.offset : archive.offset + archive.size], base_offset=archive.offset
        )
        if descriptor_consumed != archive.size:
            raise ValueError(f"descriptor slice has trailing bytes: {archive.name}")
        validate_nonoverlap(records, len(data))
        source_archives[archive.name] = (start, end, data)
        archive_resources[archive.name] = records
        for record in records:
            resource_records[f"{archive.name}/{record.name}"] = record

    writes: list[Write] = []
    target_reports: list[dict[str, object]] = []
    expected_decoded: dict[str, bytes] = {}
    new_wrappers: dict[str, bytes] = {}
    configs_by_resource: dict[str, list[dict[str, str]]] = {}
    for config in configs:
        configs_by_resource.setdefault(config["resource_id"], []).append(config)

    for resource_id, resource_configs in configs_by_resource.items():
        row = catalog[resource_id]
        record = resource_records[resource_id]
        archive_name = row["archive"]
        archive_start, _archive_end, archive_data = source_archives[archive_name]
        original_wrapper = archive_data[record.offset : record.offset + record.size]
        if sha256(original_wrapper) != row["stored_sha256"]:
            raise ValueError(f"stored resource hash mismatch: {resource_id}")
        wrapper_info = classify_wrapper(original_wrapper)
        if wrapper_info["kind"] != "compressed":
            raise ValueError(f"graphics target is not type-1 compressed: {resource_id}")
        original_decoded = decoded_payload(original_wrapper, wrapper_info)
        if sha256(original_decoded) != row["decoded_sha256"]:
            raise ValueError(f"decoded resource hash mismatch: {resource_id}")
        bitmap_info = classify_indexed_bitmap(original_decoded)
        if bitmap_info is None:
            raise ValueError(f"target is not an indexed bitmap: {resource_id}")
        pixel_size = int(bitmap_info["pixel_size"])
        width = int(bitmap_info["width"])
        height = int(bitmap_info["height"])
        allowed_indices = set(
            unpack_indices(
                original_decoded[16 : 16 + pixel_size],
                int(bitmap_info["bpp"]),
                width * height,
            )
        )
        modified_decoded = original_decoded
        canvas_reports: list[tuple[dict[str, str], dict[str, object], str]] = []
        for config in resource_configs:
            canvas_id = config["canvas_id"]
            modified_decoded, import_report = import_canvas(
                modified_decoded,
                translated_paths[canvas_id],
                config,
                row,
                allowed_indices=allowed_indices,
            )
            canvas_reports.append((config, import_report, sha256(modified_decoded)))
        new_wrapper = compressed_wrapper(modified_decoded)
        records = sorted(archive_resources[archive_name], key=lambda item: item.offset)
        position = records.index(record)
        slot_end = records[position + 1].offset if position + 1 < len(records) else len(archive_data)
        capacity = slot_end - record.offset
        new_wrappers[resource_id] = new_wrapper
        expected_decoded[resource_id] = modified_decoded
        for config, import_report, intermediate_sha256 in canvas_reports:
            canvas_id = config["canvas_id"]
            target_reports.append(
                {
                    "canvas_id": canvas_id,
                    "resource_id": resource_id,
                    "resource_canvas_count": len(resource_configs),
                    "archive": archive_name,
                    "resource_offset": record.offset,
                    "slot_capacity": capacity,
                    "fits_original_slot": len(new_wrapper) <= capacity,
                    "original_stored_size": record.size,
                    "recompressed_size": len(new_wrapper),
                    "original_decoded_sha256": sha256(original_decoded),
                    "intermediate_decoded_sha256": intermediate_sha256,
                    "modified_decoded_sha256": sha256(modified_decoded),
                    "translated_png": str(translated_paths[canvas_id]),
                    "translated_png_sha256": sha256(translated_paths[canvas_id].read_bytes()),
                    **import_report,
                    "compressor_self_roundtrip": "pass",
                }
            )

    # Rebuild every affected archive as one 0x20-aligned unit. Unchanged wrappers
    # remain byte-identical; only their descriptor offsets can move.
    affected_archives = sorted({resource_id.split("/", 1)[0] for resource_id in new_wrappers})
    rebuilt_archives: dict[str, bytes] = {}
    archive_reports: list[dict[str, object]] = []
    final_ranges = list(source.fat)
    for archive_name in affected_archives:
        archive_start, archive_end, archive_data = source_archives[archive_name]
        rebuilt = bytearray()
        records = sorted(archive_resources[archive_name], key=lambda item: item.offset)
        for record in records:
            while len(rebuilt) % 0x20:
                rebuilt.append(0xFF)
            new_offset = len(rebuilt)
            resource_id = f"{archive_name}/{record.name}"
            body = new_wrappers.get(
                resource_id, archive_data[record.offset : record.offset + record.size]
            )
            rebuilt.extend(body)
            if (new_offset, len(body)) != (record.offset, record.size):
                descriptor_offset = head_bin_start + int(record.record_offset) + 0x10
                writes.append(
                    Write(
                        owner=f"graphics:{archive_name}:descriptor:{record.name}",
                        meaning=f"head.bin offset and size for {resource_id}",
                        rom_offset=descriptor_offset,
                        expected=struct.pack("<II", record.offset, record.size),
                        replacement=struct.pack("<II", new_offset, len(body)),
                    )
                )
        minimum_size = len(rebuilt)
        target_size = max(len(archive_data), align(minimum_size, 0x20))
        rebuilt.extend(b"\xFF" * (target_size - len(rebuilt)))
        rebuilt_archives[archive_name] = bytes(rebuilt)
        file_id = file_id_for(source, f"data/pack/{archive_name}.bin")
        final_ranges[file_id] = (archive_start, archive_start + target_size)
        archive_reports.append(
            {
                "archive": archive_name,
                "file_id": file_id,
                "original_start": archive_start,
                "original_size": archive_end - archive_start,
                "rebuilt_size": target_size,
                "minimum_repacked_size": minimum_size,
            }
        )

    # If an expanded archive reaches the following NitroFS file, relocate that
    # unchanged file into the unused ROM tail and update its FAT entry.
    relocate_ids: set[int] = set()
    affected_ids = {int(row["file_id"]) for row in archive_reports}
    for row in archive_reports:
        file_id = int(row["file_id"])
        target_start, target_end = final_ranges[file_id]
        for other_id, (other_start, other_end) in enumerate(source.fat):
            if other_id == file_id or other_id in affected_ids:
                continue
            if target_start < other_end and other_start < target_end:
                relocate_ids.add(other_id)
    relocation_cursor = align(int(source.header["rom_used_size"]), 0x200)
    relocation_reports: list[dict[str, object]] = []
    for file_id in sorted(relocate_ids, key=lambda value: source.fat[value][0]):
        old_start, old_end = source.fat[file_id]
        payload = base.data[old_start:old_end]
        new_start = align(relocation_cursor, 0x200)
        new_end = new_start + len(payload)
        if new_end > len(base.data):
            raise ValueError(f"relocated NitroFS file exceeds ROM capacity: file {file_id}")
        writes.append(
            Write(
                owner=f"graphics:relocate:file_{file_id}",
                meaning=f"relocate unchanged NitroFS file {base.paths.get(file_id)}",
                rom_offset=new_start,
                expected=base.data[new_start:new_end],
                replacement=payload,
            )
        )
        final_ranges[file_id] = (new_start, new_end)
        relocation_cursor = new_end
        relocation_reports.append(
            {
                "file_id": file_id,
                "path": base.paths.get(file_id),
                "old_range": [old_start, old_end],
                "new_range": [new_start, new_end],
                "payload_sha256": sha256(payload),
            }
        )

    # Confirm all final FAT extents are disjoint before writing archive bodies.
    occupied = sorted(
        (start, end, file_id)
        for file_id, (start, end) in enumerate(final_ranges)
        if end > start
    )
    for left, right in zip(occupied, occupied[1:]):
        if left[1] > right[0]:
            raise ValueError(
                f"final NitroFS overlap: file {left[2]} ends 0x{left[1]:X}, "
                f"file {right[2]} starts 0x{right[0]:X}"
            )

    for row in archive_reports:
        archive_name = str(row["archive"])
        file_id = int(row["file_id"])
        start, end = final_ranges[file_id]
        replacement = rebuilt_archives[archive_name]
        if end - start != len(replacement):
            raise ValueError(f"rebuilt archive extent mismatch: {archive_name}")
        writes.append(
            Write(
                owner=f"graphics:archive:{archive_name}",
                meaning=f"repacked archive data/pack/{archive_name}.bin",
                rom_offset=start,
                expected=base.data[start:end],
                replacement=replacement,
            )
        )

    fat_start = int(source.header["fat"]["rom_offset"])
    for file_id, (old_range, new_range) in enumerate(zip(source.fat, final_ranges)):
        if old_range == new_range:
            continue
        writes.append(
            Write(
                owner=f"graphics:fat:file_{file_id}",
                meaning=f"FAT extent for {base.paths.get(file_id)}",
                rom_offset=fat_start + file_id * 8,
                expected=struct.pack("<II", *old_range),
                replacement=struct.pack("<II", *new_range),
            )
        )

    new_used_size = align(max(int(source.header["rom_used_size"]), relocation_cursor), 0x200)
    if new_used_size > len(base.data):
        raise ValueError("updated ROM used size exceeds physical ROM capacity")
    if new_used_size != int(source.header["rom_used_size"]):
        writes.append(
            Write(
                owner="graphics:nds_header:rom_used_size",
                meaning="NDS ROM used-size field after NitroFS relocation",
                rom_offset=0x80,
                expected=struct.pack("<I", int(source.header["rom_used_size"])),
                replacement=struct.pack("<I", new_used_size),
            )
        )
        header = bytearray(base.data[:0x160])
        struct.pack_into("<I", header, 0x80, new_used_size)
        new_crc = nds_crc16(bytes(header[:0x15E]))
        writes.append(
            Write(
                owner="graphics:nds_header:crc16",
                meaning="NDS header CRC16 after used-size update",
                rom_offset=0x15E,
                expected=base.data[0x15E:0x160],
                replacement=struct.pack("<H", new_crc),
            )
        )

    validate_writes(writes, base.data)
    output = bytearray(base.data)
    for write in sorted(writes, key=lambda row: row.rom_offset):
        output[write.rom_offset : write.rom_offset + len(write.replacement)] = write.replacement
    output_bytes = bytes(output)
    changed_count, changed_intervals = diff_intervals(base.data, output_bytes)
    allowed = set()
    for write in writes:
        allowed.update(range(write.rom_offset, write.rom_offset + len(write.replacement)))
    actual = {
        offset for offset, (left, right) in enumerate(zip(base.data, output_bytes)) if left != right
    }
    if actual != {offset for offset in allowed if base.data[offset] != output_bytes[offset]}:
        raise ValueError("final ROM diff is not exactly explained by Expected Writes")

    # Read back every changed resource through the rebuilt directories and wrapper decoder.
    rebuilt_head_bin = output_bytes[head_bin_start:head_bin_end]
    for resource_id, expected in expected_decoded.items():
        archive_name, resource_name = resource_id.split("/", 1)
        archive = archive_by_name[archive_name]
        records, descriptor_consumed = parse_directory(
            rebuilt_head_bin[archive.offset : archive.offset + archive.size],
            base_offset=archive.offset,
        )
        if descriptor_consumed != archive.size:
            raise ValueError(f"rebuilt descriptor parse mismatch: {archive_name}")
        record = next((row for row in records if row.name == resource_name), None)
        if record is None:
            raise ValueError(f"rebuilt target missing: {resource_id}")
        archive_file_id = file_id_for(source, f"data/pack/{archive_name}.bin")
        archive_start, archive_end = final_ranges[archive_file_id]
        archive_data = output_bytes[archive_start:archive_end]
        body = archive_data[record.offset : record.offset + record.size]
        wrapper = classify_wrapper(body)
        decoded = decoded_payload(body, wrapper)
        if decoded != expected:
            raise ValueError(f"rebuilt resource readback mismatch: {resource_id}")

    report = {
        "schema_version": 1,
        "status": "full_translation_plus_graphics_pack_qa_built_static_verified",
        "source": {
            "path": str(args.source_rom),
            "sha256": sha256(source.data),
            "size": len(source.data),
            "config": str(args.source_config),
            "config_sha256": sha256(source_config_bytes),
        },
        "base_translation": {
            "path": str(args.base_rom),
            "sha256": sha256(base.data),
            "report": str(args.base_report),
            "report_sha256": sha256(base_report_bytes),
        },
        "graphics": {
            "pack_root": str(args.pack_root),
            "pack_report_sha256": sha256(pack_report_bytes),
            "canvas_config": str(args.canvas_config),
            "canvas_config_sha256": sha256(args.canvas_config.read_bytes()),
            "translated_dir": str(args.translated_dir),
            "target_count": len(target_reports),
            "targets": target_reports,
            "archives": archive_reports,
            "relocations": relocation_reports,
        },
        "expected_writes": [
            {
                "owner": write.owner,
                "meaning": write.meaning,
                "rom_offset": write.rom_offset,
                "size": len(write.replacement),
                "expected_sha256": sha256(write.expected),
                "replacement_sha256": sha256(write.replacement),
            }
            for write in sorted(writes, key=lambda row: row.rom_offset)
        ],
        "output": {
            "path": str(args.output_rom),
            "sha256": sha256(output_bytes),
            "size": len(output_bytes),
        },
        "changed_from_base": {
            "byte_count": changed_count,
            "intervals": changed_intervals,
        },
        "gates": {
            "source_identity": "pass",
            "base_translation_report_identity": "pass",
            "base_pack_byte_identity_to_source": "pass",
            "canvas_population": f"pass_{len(target_reports)}_of_{len(target_reports)}",
            "decoded_header_palette_protection": "pass",
            "palette_index_subset": "pass",
            "type1_self_roundtrip": f"pass_{len(target_reports)}_of_{len(target_reports)}",
            "archive_repack_0x20_alignment": f"pass_{len(archive_reports)}_of_{len(archive_reports)}",
            "nitrofs_relocation": (
                f"pass_{len(relocation_reports)}_file" if relocation_reports else "not_required"
            ),
            "head_bin_descriptor_readback": f"pass_{len(target_reports)}_of_{len(target_reports)}",
            "decoded_resource_readback": f"pass_{len(target_reports)}_of_{len(target_reports)}",
            "nds_physical_size_fnt_and_nonowned_header_fields_preserved": "pass",
            "fat_and_header_integrity_after_relocation": "pass",
            "registered_write_boundary": "pass",
        },
        "not_proved": [
            "ARM9 decoder compatibility during emulator execution",
            "runtime palette and layer composition for the 13 edited graphics",
            "menu navigation and pda return-button state transitions",
            "whole-game graphics population completeness",
        ],
    }
    args.output_rom.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output_rom.write_bytes(output_bytes)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-rom", type=Path, required=True)
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--base-rom", type=Path, required=True)
    parser.add_argument("--base-report", type=Path, required=True)
    parser.add_argument("--pack-root", type=Path, required=True)
    parser.add_argument("--canvas-config", type=Path, required=True)
    parser.add_argument("--translated-dir", type=Path, required=True)
    parser.add_argument("--output-rom", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = build(args)
    print(
        json.dumps(
            {
                "status": result["status"],
                "target_count": result["graphics"]["target_count"],
                "output": result["output"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
