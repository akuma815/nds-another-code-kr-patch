#!/usr/bin/env python3
"""Build a development ROM proving pointer-based text relocation from the final CSV."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from analyze_game_font import GLYPH_SIZE, index_to_sjis, sjis_to_index
from analyze_translation_relocation import (
    ACCEPTED_POPULATIONS,
    merge_zero_aligned_slots,
    pack_records_into_regions,
)
from build_dialogue_e2e import DIRECTIVE_RE, encode_literal, validate_selected_font
from build_font_poc import load_target_glyph, pack_16x16_2bpp
from generate_hangul_mapping import allocate_mappings, first_occurrence_hangul
from nds_inventory import NdsRom, diff_intervals


FONT_PATH = "data/font_lc.bin"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def component_info(rom: NdsRom, component: str) -> tuple[int, int, int]:
    if component == "arm9":
        info = rom.header["arm9"]
        return info["rom_offset"], info["size"], info["load_address"]
    overlay = next(
        (
            row
            for row in rom.arm9_overlays
            if component
            == f"arm9_overlay_{row['overlay_id']:03d}_file_{row['file_id']:03d}"
        ),
        None,
    )
    if overlay is None:
        raise ValueError(f"component not found in ARM9 overlays: {component}")
    if overlay["compressed_size"] != 0 or overlay["flags"] != 0:
        raise ValueError(f"compressed or flagged overlay is outside this PoC: {component}")
    start, end = rom.fat[overlay["file_id"]]
    if end - start != overlay["ram_size"]:
        raise ValueError(f"overlay file/RAM size mismatch: {component}")
    return start, end - start, overlay["ram_address"]


def allocate_poc_mapping(
    selected_entries: list[dict],
    pool: dict,
    existing: dict,
    font_config: dict,
    source_records: list[dict],
    build_id: str,
) -> dict:
    if pool["source_sha256"] != existing["source_sha256"]:
        raise ValueError("mapping pool/existing mapping source mismatch")
    if pool["pool_id"] != existing["pool_id"]:
        raise ValueError("mapping pool identity mismatch")
    if font_config["source"]["sha256"] != existing["font_sha256"]:
        raise ValueError("existing mapping/font identity mismatch")
    used_windows = {
        raw[offset : offset + 2]
        for record in source_records
        for raw in [bytes.fromhex(record["raw_hex"])]
        for offset in range(max(0, len(raw) - 1))
    }
    candidates: list[tuple[int, int]] = []
    for index in range(pool["start_glyph_index"], pool["end_glyph_index"] - 1, -1):
        code = index_to_sjis(index)
        if code.to_bytes(2, "big") not in used_windows:
            candidates.append((code, index))
    characters = first_occurrence_hangul(selected_entries)
    mappings = allocate_mappings(characters, candidates, existing)
    return {
        "schema_version": 1,
        "mapping_id": f"{build_id}-mapping",
        "status": "development_relocation_poc_mapping_not_whole_game_approved",
        "source_sha256": pool["source_sha256"],
        "build_id": build_id,
        "pool_id": pool["pool_id"],
        "font_sha256": font_config["source"]["sha256"],
        "character_count": len(mappings),
        "available_pool_count": len(candidates),
        "mappings": mappings,
    }


def compose_cluster_payloads(
    regions: list[dict], placements: list[dict], payload_by_id: dict[str, bytes]
) -> list[dict]:
    by_cluster: dict[int, list[dict]] = {}
    for placement in placements:
        by_cluster.setdefault(placement["cluster_id"], []).append(placement)
    results = []
    seen: set[str] = set()
    for region in regions:
        payload = bytearray(region["capacity"])
        for placement in sorted(
            by_cluster.get(region["cluster_id"], []), key=lambda row: row["target_offset"]
        ):
            text_id = placement["translation_text_id"]
            if text_id in seen:
                raise ValueError(f"duplicate packed text ID: {text_id}")
            seen.add(text_id)
            encoded = payload_by_id[text_id]
            start = placement["target_offset"] - region["start"]
            end = start + len(encoded)
            if end >= len(payload):
                raise ValueError(f"packed record exceeds cluster or lacks NUL: {text_id}")
            if any(payload[start : end + 1]):
                raise ValueError(f"packed record collision: {text_id}")
            payload[start:end] = encoded
            payload[end] = 0
        results.append(
            {
                "cluster_id": region["cluster_id"],
                "start": region["start"],
                "end": region["end"],
                "member_text_ids": region["member_text_ids"],
                "payload": bytes(payload),
            }
        )
    if seen != set(payload_by_id):
        missing = sorted(set(payload_by_id) - seen)
        raise ValueError(f"records missing from packed clusters: {missing}")
    return results


def validate_nonoverlapping_writes(writes: list[dict]) -> None:
    ordered = sorted(writes, key=lambda row: (row["rom_offset"], row["size"], row["owner"]))
    for left, right in zip(ordered, ordered[1:]):
        if right["rom_offset"] < left["rom_offset"] + left["size"]:
            raise ValueError(
                f"Expected Write overlap: {left['owner']} and {right['owner']} at "
                f"0x{right['rom_offset']:X}"
            )


def build(args: argparse.Namespace) -> dict:
    outputs = (args.output_rom, args.output_mapping, args.report)
    existing_outputs = [str(path) for path in outputs if path.exists()]
    if existing_outputs and not args.force:
        raise FileExistsError(f"refusing to overwrite existing outputs: {existing_outputs}")

    config = json.loads(args.config.read_text(encoding="utf-8"))
    pool = json.loads(args.pool.read_text(encoding="utf-8"))
    existing_mapping = json.loads(args.existing_mapping.read_text(encoding="utf-8"))
    references = json.loads(args.reference_report.read_text(encoding="utf-8"))
    scripts = json.loads(args.script_report.read_text(encoding="utf-8"))["records"]
    auxiliary = json.loads(args.aux_report.read_text(encoding="utf-8"))["records"]
    font_config = validate_selected_font(args.font_png, args.font_config)
    translated_rows = read_csv(args.translated_csv)
    translated_hash = sha256(args.translated_csv.read_bytes())
    if translated_hash != config["translated_csv_sha256"]:
        raise ValueError(
            f"translated CSV SHA-256 mismatch: expected {config['translated_csv_sha256']}, "
            f"got {translated_hash}"
        )

    rom = NdsRom(args.source_rom)
    source_hash = sha256(rom.data)
    if source_hash != config["source_sha256"] or source_hash != references["source_sha256"]:
        raise ValueError("source ROM identity mismatch")

    component = config["component"]
    selected_ids = list(config["selected_text_ids"])
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("duplicate selected text ID")
    rows = [
        row
        for row in translated_rows
        if row["component"] == component and row["population"] in ACCEPTED_POPULATIONS
    ]
    row_by_id = {row["text_id"]: row for row in rows}
    if set(selected_ids) - set(row_by_id):
        raise ValueError(f"selected IDs missing from component: {sorted(set(selected_ids) - set(row_by_id))}")
    source_by_id = {row["text_id"]: row for row in scripts + auxiliary}
    reference_by_id = {row["text_id"]: row for row in references["catalog"]}
    for row in rows:
        source = source_by_id.get(row["text_id"])
        if source is None:
            raise ValueError(f"source record missing: {row['text_id']}")
        if bytes.fromhex(row["source_raw_hex"]) != bytes.fromhex(source["raw_hex"]):
            raise ValueError(f"protected source raw mismatch: {row['text_id']}")
        if row["source_text"] != source["cp932"]:
            raise ValueError(f"protected source text mismatch: {row['text_id']}")

    selected_entries = [
        {
            "text_id": text_id,
            "translation_text": row_by_id[text_id]["translation_text"],
        }
        for text_id in selected_ids
    ]
    if any(not entry["translation_text"].strip() for entry in selected_entries):
        raise ValueError("selected translation is blank")
    mapping_data = allocate_poc_mapping(
        selected_entries,
        pool,
        existing_mapping,
        font_config,
        scripts + auxiliary,
        config["build_id"],
    )
    mapping = {row["character"]: int(row["code"], 16) for row in mapping_data["mappings"]}

    payload_by_id: dict[str, bytes] = {}
    for row in rows:
        text_id = row["text_id"]
        if text_id in selected_ids:
            if DIRECTIVE_RE.findall(row["source_text"]) != DIRECTIVE_RE.findall(
                row["translation_text"]
            ):
                raise ValueError(f"directive sequence mismatch: {text_id}")
            try:
                payload_by_id[text_id] = encode_literal(row["translation_text"], mapping)
            except UnicodeEncodeError as exc:
                raise ValueError(f"unmapped translation character: {text_id}: {exc}") from exc
        else:
            payload_by_id[text_id] = bytes.fromhex(row["source_raw_hex"])

    component_rom_start, component_size, component_load = component_info(rom, component)
    component_data = rom.data[component_rom_start : component_rom_start + component_size]
    intervals = []
    pointer_ranges = []
    for row in rows:
        offset = int(row["component_offset_hex"], 16)
        raw = bytes.fromhex(row["source_raw_hex"])
        end = offset + len(raw)
        if component_data[offset:end] != raw:
            raise ValueError(f"component Expected Write source mismatch: {row['text_id']}")
        if end >= len(component_data) or component_data[end] != 0:
            raise ValueError(f"source NUL boundary mismatch: {row['text_id']}")
        intervals.append((offset, end + 1, row["text_id"]))
        reference = reference_by_id.get(row["text_id"])
        if reference is None or reference["pointer_encoding"] != "u32le_absolute_ram":
            raise ValueError(f"missing or unsupported reference: {row['text_id']}")
        expected_pointer = (component_load + offset).to_bytes(4, "little")
        if bytes.fromhex(reference["pointer_raw_hex"]) != expected_pointer:
            raise ValueError(f"reference target mismatch: {row['text_id']}")
        for pointer_offset in reference["reference_offsets"]:
            if component_data[pointer_offset : pointer_offset + 4] != expected_pointer:
                raise ValueError(f"stored pointer mismatch: {row['text_id']}@0x{pointer_offset:X}")
            pointer_ranges.append((pointer_offset, pointer_offset + 4))

    regions, borrowed_zero_bytes = merge_zero_aligned_slots(
        component_data,
        intervals,
        pointer_ranges,
        max_gap=int(config["packing"]["max_zero_alignment_gap"]),
    )
    packing_rows = [
        {
            "text_id": row["text_id"],
            "translation_length": len(payload_by_id[row["text_id"]]),
        }
        for row in rows
    ]
    placements, failures = pack_records_into_regions(packing_rows, regions)
    if failures:
        raise ValueError(f"component text packing failed: {failures}")
    placement_by_id = {row["translation_text_id"]: row for row in placements}
    cluster_payloads = compose_cluster_payloads(regions, placements, payload_by_id)

    writes: list[dict] = []
    write_payloads: list[tuple[int, bytes]] = []
    for cluster in cluster_payloads:
        rom_offset = component_rom_start + cluster["start"]
        expected = rom.data[rom_offset : rom_offset + len(cluster["payload"])]
        replacement = cluster["payload"]
        if expected == replacement:
            continue
        writes.append(
            {
                "owner": "relocated_text_cluster",
                "component": component,
                "cluster_id": cluster["cluster_id"],
                "component_offset": cluster["start"],
                "rom_offset": rom_offset,
                "size": len(replacement),
                "expected_sha256": sha256(expected),
                "replacement_sha256": sha256(replacement),
                "member_text_ids": cluster["member_text_ids"],
            }
        )
        write_payloads.append((rom_offset, replacement))

    pointer_write_count = 0
    for row in rows:
        text_id = row["text_id"]
        reference = reference_by_id[text_id]
        old_offset = int(row["component_offset_hex"], 16)
        new_offset = placement_by_id[text_id]["target_offset"]
        expected_pointer = (component_load + old_offset).to_bytes(4, "little")
        replacement_pointer = (component_load + new_offset).to_bytes(4, "little")
        for pointer_offset in reference["reference_offsets"]:
            if expected_pointer == replacement_pointer:
                continue
            rom_offset = component_rom_start + pointer_offset
            if rom.data[rom_offset : rom_offset + 4] != expected_pointer:
                raise ValueError(f"pointer Expected Write mismatch: {text_id}@0x{pointer_offset:X}")
            writes.append(
                {
                    "owner": "relocated_text_pointer",
                    "text_id": text_id,
                    "component": component,
                    "component_offset": pointer_offset,
                    "rom_offset": rom_offset,
                    "size": 4,
                    "expected_hex": expected_pointer.hex().upper(),
                    "replacement_hex": replacement_pointer.hex().upper(),
                    "old_target_offset": old_offset,
                    "new_target_offset": new_offset,
                }
            )
            write_payloads.append((rom_offset, replacement_pointer))
            pointer_write_count += 1

    font_file_id = next((file_id for file_id, path in rom.paths.items() if path == FONT_PATH), None)
    if font_file_id is None:
        raise ValueError(f"NitroFS font not found: {FONT_PATH}")
    font_start, font_end = rom.fat[font_file_id]
    for mapping_row in mapping_data["mappings"]:
        code = int(mapping_row["code"], 16)
        if sjis_to_index(code) != mapping_row["glyph_index"]:
            raise ValueError(f"mapping glyph index mismatch: {mapping_row['character']}")
        glyph = pack_16x16_2bpp(load_target_glyph(args.font_png, mapping_row["character"]))
        rom_offset = font_start + mapping_row["glyph_index"] * GLYPH_SIZE
        if rom_offset + GLYPH_SIZE > font_end:
            raise ValueError("font glyph write exceeds font_lc.bin")
        expected = rom.data[rom_offset : rom_offset + GLYPH_SIZE]
        writes.append(
            {
                "owner": "hangul_glyph",
                "character": mapping_row["character"],
                "sjis_code": mapping_row["code"],
                "glyph_index": mapping_row["glyph_index"],
                "component": FONT_PATH,
                "rom_offset": rom_offset,
                "size": GLYPH_SIZE,
                "expected_sha256": sha256(expected),
                "replacement_sha256": sha256(glyph),
            }
        )
        write_payloads.append((rom_offset, glyph))

    validate_nonoverlapping_writes(writes)
    output = bytearray(rom.data)
    for rom_offset, replacement in write_payloads:
        output[rom_offset : rom_offset + len(replacement)] = replacement
    output_bytes = bytes(output)

    allowed_offsets: set[int] = set()
    for write in writes:
        allowed_offsets.update(range(write["rom_offset"], write["rom_offset"] + write["size"]))
    changed_offsets = [
        index
        for index, (source_byte, output_byte) in enumerate(zip(rom.data, output_bytes))
        if source_byte != output_byte
    ]
    if any(offset not in allowed_offsets for offset in changed_offsets):
        raise AssertionError("unregistered ROM difference")

    output_component = output_bytes[component_rom_start : component_rom_start + component_size]
    for row in rows:
        text_id = row["text_id"]
        placement = placement_by_id[text_id]
        new_offset = placement["target_offset"]
        payload = payload_by_id[text_id]
        if output_component[new_offset : new_offset + len(payload)] != payload:
            raise AssertionError(f"relocated payload readback mismatch: {text_id}")
        if output_component[new_offset + len(payload)] != 0:
            raise AssertionError(f"relocated payload NUL mismatch: {text_id}")
        expected_pointer = (component_load + new_offset).to_bytes(4, "little")
        for pointer_offset in reference_by_id[text_id]["reference_offsets"]:
            if output_component[pointer_offset : pointer_offset + 4] != expected_pointer:
                raise AssertionError(f"relocated pointer readback mismatch: {text_id}")

    changed_count, changed_intervals = diff_intervals(rom.data, output_bytes)
    if changed_count != len(changed_offsets):
        raise AssertionError("diff count disagreement")
    selected_report = []
    for text_id in selected_ids:
        row = row_by_id[text_id]
        selected_report.append(
            {
                "text_id": text_id,
                "source_text": row["source_text"],
                "translation_text": row["translation_text"],
                "source_byte_length": int(row["source_byte_length"]),
                "translation_byte_length": len(payload_by_id[text_id]),
                "old_component_offset": int(row["component_offset_hex"], 16),
                "new_component_offset": placement_by_id[text_id]["target_offset"],
                "directives": DIRECTIVE_RE.findall(row["source_text"]),
            }
        )

    report = {
        "schema_version": 1,
        "gate_id": config["gate_id"],
        "build_id": config["build_id"],
        "status": "static_build_pass_runtime_pending",
        "build_class": config["build_class"],
        "source": {"path": str(args.source_rom), "sha256": source_hash, "size": len(rom.data)},
        "translated_ssot": {
            "path": str(args.translated_csv),
            "sha256": translated_hash,
            "modified_by_build": False,
        },
        "config": {"path": str(args.config), "sha256": sha256(args.config.read_bytes())},
        "font_source": {
            "path": str(args.font_png),
            "sha256": font_config["source"]["sha256"],
            "selection_status": font_config["selection_status"],
        },
        "mapping": {
            "path": str(args.output_mapping),
            "mapping_id": mapping_data["mapping_id"],
            "character_count": mapping_data["character_count"],
            "available_pool_count": mapping_data["available_pool_count"],
        },
        "component": {
            "name": component,
            "rom_offset": component_rom_start,
            "size": component_size,
            "load_address": component_load,
            "record_count": len(rows),
            "cluster_count": len(regions),
            "borrowed_zero_alignment_bytes": borrowed_zero_bytes,
            "repacked_record_count": len(placements),
            "pointer_write_count": pointer_write_count,
        },
        "selected_entries": selected_report,
        "output": {
            "path": str(args.output_rom),
            "sha256": sha256(output_bytes),
            "size": len(output_bytes),
        },
        "changed_byte_count": changed_count,
        "changed_intervals": changed_intervals,
        "registered_write_count": len(writes),
        "writes": writes,
        "runtime_criteria": config["runtime_gate"],
        "proved_static": [
            "final CSV identity and protected source fields",
            "selected directive sequence and arguments",
            "all accepted component records fit inside confirmed text regions plus <=3-byte zero alignment",
            "all cataloged direct pointers in the component point to the relocated payloads",
            "ROM size and every byte outside registered Expected Writes are preserved",
        ],
        "not_proved": [
            "runtime NUL termination and progression after relocated records",
            "whole-game relocation and auxiliary consumer behavior",
            "whole-game safe code pool and 824-syllable coverage",
        ],
    }

    args.output_rom.parent.mkdir(parents=True, exist_ok=True)
    args.output_mapping.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output_rom.write_bytes(output_bytes)
    args.output_mapping.write_text(
        json.dumps(mapping_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-rom", type=Path, required=True)
    parser.add_argument("--translated-csv", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--existing-mapping", type=Path, required=True)
    parser.add_argument("--font-png", type=Path, required=True)
    parser.add_argument("--font-config", type=Path, required=True)
    parser.add_argument("--script-report", type=Path, required=True)
    parser.add_argument("--aux-report", type=Path, required=True)
    parser.add_argument("--reference-report", type=Path, required=True)
    parser.add_argument("--output-rom", type=Path, required=True)
    parser.add_argument("--output-mapping", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    report = build(args)
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": report["output"],
                "mapping": report["mapping"],
                "component": report["component"],
                "selected_entries": report["selected_entries"],
                "changed_byte_count": report["changed_byte_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
