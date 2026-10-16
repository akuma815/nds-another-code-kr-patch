#!/usr/bin/env python3
"""Incrementally inject the approved pda/p02 preview into an existing QA ROM."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct

from PIL import Image

from analyze_pack_resources import (
    WRAPPER_PREFIX,
    classify_wrapper,
    compress_type1,
    decoded_payload,
    parse_directory,
    validate_nonoverlap,
)
from nds_inventory import NdsRom, diff_intervals


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


@dataclass(frozen=True)
class Write:
    owner: str
    meaning: str
    rom_offset: int
    expected: bytes
    replacement: bytes


def file_bytes(rom: NdsRom, path: str) -> tuple[int, int, int, bytes]:
    matches = [file_id for file_id, candidate in rom.paths.items() if candidate == path]
    if len(matches) != 1:
        raise ValueError(f"expected one NitroFS file for {path}, found {len(matches)}")
    file_id = matches[0]
    start, end = rom.fat[file_id]
    return file_id, start, end, rom.data[start:end]


def compressed_wrapper(decoded: bytes) -> bytes:
    payload = compress_type1(decoded)
    wrapper = WRAPPER_PREFIX + b"\x01" + struct.pack("<III", len(decoded), len(payload), 0) + payload
    info = classify_wrapper(wrapper)
    if info["kind"] != "compressed" or decoded_payload(wrapper, info) != decoded:
        raise ValueError("type-1 compressor self-roundtrip failed")
    return wrapper


def validate_writes(writes: list[Write], source: bytes) -> None:
    previous_end = -1
    for write in sorted(writes, key=lambda row: row.rom_offset):
        end = write.rom_offset + len(write.replacement)
        if write.rom_offset < previous_end:
            raise ValueError(f"overlapping Expected Writes at 0x{write.rom_offset:X}")
        if len(write.expected) != len(write.replacement):
            raise ValueError(f"Expected Write changed length: {write.owner}")
        if source[write.rom_offset:end] != write.expected:
            raise ValueError(f"Expected Write source mismatch: {write.owner}")
        previous_end = end


def pack_4bpp(values: list[int]) -> bytes:
    if len(values) % 2 or any(not 0 <= value <= 15 for value in values):
        raise ValueError("invalid 4bpp pixel values")
    return bytes(values[index] | (values[index + 1] << 4) for index in range(0, len(values), 2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-rom", type=Path, required=True)
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--base-rom", type=Path, required=True)
    parser.add_argument("--base-report", type=Path, required=True)
    parser.add_argument("--pack-root", type=Path, required=True)
    parser.add_argument("--preview-report", type=Path, required=True)
    parser.add_argument("--translated-png", type=Path, required=True)
    parser.add_argument("--output-rom", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.output_rom.exists() or args.report.exists():
        raise FileExistsError("refusing to overwrite an existing output or report")

    source_config_bytes = args.source_config.read_bytes()
    source_config = json.loads(source_config_bytes.decode("utf-8"))
    source = NdsRom(args.source_rom)
    expected_source = source_config["source"]
    if len(source.data) != int(expected_source["size"]) or sha256(source.data) != expected_source["sha256"]:
        raise ValueError("source ROM identity mismatch")

    base_report_bytes = args.base_report.read_bytes()
    base_report = json.loads(base_report_bytes.decode("utf-8"))
    base = NdsRom(args.base_rom)
    if sha256(base.data) != base_report["output"]["sha256"]:
        raise ValueError("base ROM does not match its build report")
    if len(base.data) != len(source.data) or base.paths != source.paths:
        raise ValueError("base ROM changed the NDS filesystem identity")

    preview_report_bytes = args.preview_report.read_bytes()
    preview_report = json.loads(preview_report_bytes.decode("utf-8"))
    if preview_report["status"] != "png_preview_only_pending_user_approval":
        raise ValueError("preview report is not the approved PNG-only candidate")
    expected_png_hash = preview_report["outputs"]["indexed_candidate_sha256"]
    translated_png_bytes = args.translated_png.read_bytes()
    if sha256(translated_png_bytes) != expected_png_hash:
        raise ValueError("translated PNG does not match the approved preview report")

    with (args.pack_root / "catalog.csv").open(encoding="utf-8-sig", newline="") as handle:
        catalog_rows = list(csv.DictReader(handle))
    catalog_matches = [row for row in catalog_rows if row["resource_id"] == "pda/p02"]
    if len(catalog_matches) != 1:
        raise ValueError("pack catalog does not contain exactly one pda/p02")
    catalog = catalog_matches[0]

    _head_inf_id, _head_inf_start, _head_inf_end, head_inf = file_bytes(base, "data/pack/head.inf")
    _head_bin_id, head_bin_start, head_bin_end, head_bin = file_bytes(base, "data/pack/head.bin")
    pda_file_id, pda_start, pda_end, pda_data = file_bytes(base, "data/pack/pda.bin")
    archives, head_inf_consumed = parse_directory(head_inf)
    if head_inf_consumed != len(head_inf):
        raise ValueError("base head.inf has trailing bytes")
    validate_nonoverlap(archives, len(head_bin))
    pda_archive = next((row for row in archives if row.name == "pda"), None)
    if pda_archive is None:
        raise ValueError("pda archive descriptor is missing")
    records, descriptor_consumed = parse_directory(
        head_bin[pda_archive.offset : pda_archive.offset + pda_archive.size],
        base_offset=pda_archive.offset,
    )
    if descriptor_consumed != pda_archive.size:
        raise ValueError("pda descriptor slice has trailing bytes")
    validate_nonoverlap(records, len(pda_data))
    record = next((row for row in records if row.name == "p02"), None)
    if record is None:
        raise ValueError("pda/p02 record is missing")
    ordered = sorted(records, key=lambda row: row.offset)
    position = ordered.index(record)
    slot_end = ordered[position + 1].offset if position + 1 < len(ordered) else len(pda_data)
    slot_capacity = slot_end - record.offset

    original_wrapper = pda_data[record.offset : record.offset + record.size]
    if sha256(original_wrapper) != catalog["stored_sha256"]:
        raise ValueError("base pda/p02 wrapper differs from the traced source")
    original_info = classify_wrapper(original_wrapper)
    original_decoded = decoded_payload(original_wrapper, original_info)
    if sha256(original_decoded) != catalog["decoded_sha256"]:
        raise ValueError("base pda/p02 decoded bytes differ from the traced source")
    flags, width, height, pixel_size, palette_size = struct.unpack_from("<IHHII", original_decoded, 0)
    if (flags, width, height, pixel_size, palette_size) != (0x00100000, 128, 32, 0x800, 0x20):
        raise ValueError("pda/p02 is not the validated palette-first bitmap")

    with Image.open(args.translated_png) as image:
        if image.mode != "P" or image.size != (width, height):
            raise ValueError("translated pda/p02 PNG contract mismatch")
        indices = list(image.get_flattened_data())
    original_pixels = original_decoded[0x30:]
    original_indices = []
    for value in original_pixels:
        original_indices.extend((value & 0x0F, value >> 4))
    if not set(indices) <= set(original_indices):
        raise ValueError("translated pda/p02 uses foreign palette indices")
    rebuilt_pixels = pack_4bpp(indices)
    modified_decoded = original_decoded[:0x30] + rebuilt_pixels
    if modified_decoded[:0x30] != original_decoded[:0x30]:
        raise ValueError("pda/p02 header or palette changed")
    decoded_changed_count, decoded_intervals = diff_intervals(original_decoded, modified_decoded)
    if not decoded_changed_count or any(row["start"] < 0x30 for row in decoded_intervals):
        raise ValueError("pda/p02 decoded diff escaped the texture payload")

    new_wrapper = compressed_wrapper(modified_decoded)
    if len(new_wrapper) > slot_capacity:
        raise ValueError(
            f"recompressed pda/p02 exceeds its slot: {len(new_wrapper)} > {slot_capacity}"
        )
    wrapper_write_expected = pda_data[record.offset : record.offset + record.size]
    wrapper_write_replacement = new_wrapper + wrapper_write_expected[len(new_wrapper) :]
    writes = [
        Write(
            owner="photo-command:pda/p02:wrapper",
            meaning="approved palette-first pda/p02 compressed resource",
            rom_offset=pda_start + record.offset,
            expected=wrapper_write_expected,
            replacement=wrapper_write_replacement,
        ),
        Write(
            owner="photo-command:pda/p02:descriptor-size",
            meaning="pda/p02 compressed resource size in head.bin",
            rom_offset=head_bin_start + int(record.record_offset) + 0x14,
            expected=struct.pack("<I", record.size),
            replacement=struct.pack("<I", len(new_wrapper)),
        ),
    ]
    validate_writes(writes, base.data)
    output = bytearray(base.data)
    for write in sorted(writes, key=lambda row: row.rom_offset):
        output[write.rom_offset : write.rom_offset + len(write.replacement)] = write.replacement
    output_bytes = bytes(output)

    changed_count, changed_intervals = diff_intervals(base.data, output_bytes)
    allowed_changed = set()
    for write in writes:
        allowed_changed.update(
            offset
            for offset in range(write.rom_offset, write.rom_offset + len(write.replacement))
            if base.data[offset] != output_bytes[offset]
        )
    actual_changed = {
        offset for offset, (before, after) in enumerate(zip(base.data, output_bytes, strict=True))
        if before != after
    }
    if actual_changed != allowed_changed:
        raise ValueError("final ROM diff is not exactly explained by Expected Writes")

    rebuilt = NdsRom.from_bytes(output_bytes)
    if rebuilt.header != base.header or rebuilt.fat != base.fat or rebuilt.paths != base.paths:
        raise ValueError("incremental build changed NDS header, FAT, or FNT")
    _, rebuilt_head_start, rebuilt_head_end, rebuilt_head = file_bytes(rebuilt, "data/pack/head.bin")
    _, rebuilt_pda_start, rebuilt_pda_end, rebuilt_pda = file_bytes(rebuilt, "data/pack/pda.bin")
    if (rebuilt_head_start, rebuilt_head_end, rebuilt_pda_start, rebuilt_pda_end) != (
        head_bin_start,
        head_bin_end,
        pda_start,
        pda_end,
    ):
        raise ValueError("incremental build moved head.bin or pda.bin")
    rebuilt_records, rebuilt_consumed = parse_directory(
        rebuilt_head[pda_archive.offset : pda_archive.offset + pda_archive.size],
        base_offset=pda_archive.offset,
    )
    if rebuilt_consumed != pda_archive.size:
        raise ValueError("rebuilt pda descriptor parse mismatch")
    rebuilt_record = next(row for row in rebuilt_records if row.name == "p02")
    if rebuilt_record.offset != record.offset or rebuilt_record.size != len(new_wrapper):
        raise ValueError("rebuilt pda/p02 descriptor mismatch")
    readback_wrapper = rebuilt_pda[
        rebuilt_record.offset : rebuilt_record.offset + rebuilt_record.size
    ]
    readback_decoded = decoded_payload(readback_wrapper, classify_wrapper(readback_wrapper))
    if readback_decoded != modified_decoded:
        raise ValueError("rebuilt pda/p02 readback mismatch")
    for before, after in zip(records, rebuilt_records, strict=True):
        if before.name == "p02":
            continue
        if (before.name, before.offset, before.size) != (after.name, after.offset, after.size):
            raise ValueError(f"unrelated pda descriptor changed: {before.name}")

    report = {
        "schema_version": 1,
        "status": "v31_photo_command_p02_incremental_rom_built_static_verified",
        "source": {
            "path": str(args.source_rom),
            "sha256": sha256(source.data),
            "config": str(args.source_config),
            "config_sha256": sha256(source_config_bytes),
        },
        "base": {
            "path": str(args.base_rom),
            "sha256": sha256(base.data),
            "report": str(args.base_report),
            "report_sha256": sha256(base_report_bytes),
        },
        "approved_preview": {
            "report": str(args.preview_report),
            "report_sha256": sha256(preview_report_bytes),
            "translated_png": str(args.translated_png),
            "translated_png_sha256": sha256(translated_png_bytes),
        },
        "resource": {
            "resource_id": "pda/p02",
            "pda_file_id": pda_file_id,
            "archive_offset": record.offset,
            "slot_capacity": slot_capacity,
            "original_stored_size": record.size,
            "recompressed_size": len(new_wrapper),
            "original_wrapper_sha256": sha256(original_wrapper),
            "replacement_wrapper_sha256": sha256(new_wrapper),
            "original_decoded_sha256": sha256(original_decoded),
            "modified_decoded_sha256": sha256(modified_decoded),
            "decoded_changed_byte_count": decoded_changed_count,
            "decoded_changed_intervals": decoded_intervals,
            "layout": "palette-first 16-byte header + 32-byte palette + linear 4bpp texture",
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
            "size": len(output_bytes),
            "sha256": sha256(output_bytes),
        },
        "changed_from_base": {
            "byte_count": changed_count,
            "intervals": changed_intervals,
        },
        "gates": {
            "source_identity": "pass",
            "base_report_identity": "pass",
            "approved_preview_identity": "pass",
            "pda_p02_live_traced_source_identity": "pass",
            "palette_first_texture_only_edit": "pass",
            "palette_index_subset": "pass",
            "type1_self_roundtrip": "pass",
            "slot_capacity": f"pass_{len(new_wrapper)}_of_{slot_capacity}_bytes",
            "registered_write_boundary": "pass",
            "head_bin_descriptor_readback": "pass",
            "decoded_resource_readback": "pass",
            "unrelated_pda_descriptors_preserved": "pass",
            "nds_header_fat_fnt_and_physical_size_preserved": "pass",
        },
        "runtime_verification": "not_run",
        "not_proved": [
            "melonDS consumption of the rebuilt pda/p02 texture",
            "on-screen Korean glyph readability and alignment",
            "photo-screen frame, arrow, and adjacent button preservation at runtime",
        ],
    }
    args.output_rom.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output_rom.write_bytes(output_bytes)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": report["status"], "output": report["output"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
