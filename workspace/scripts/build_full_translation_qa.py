#!/usr/bin/env python3
"""Build a whole-catalog Korean QA ROM with dual main-font coverage.

This is intentionally a QA candidate, not a release builder.  It consumes the
user-edited final CSV unchanged, repacks every accepted record inside its
original ARM9/overlay text clusters, updates every cataloged direct pointer,
and writes the deterministic, translation-derived glyph mapping to both runtime-switchable
280000-byte fonts.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from functools import lru_cache
from pathlib import Path

from analyze_game_font import GLYPH_SIZE, sjis_to_index
from analyze_translation_relocation import (
    ACCEPTED_POPULATIONS,
    ENCODING_ALIAS_CANDIDATES,
    apply_alias_candidates,
    merge_zero_aligned_slots,
    pack_records_into_regions,
)
from build_dialogue_e2e import DIRECTIVE_RE, encode_literal, validate_selected_font
from build_font_poc import load_target_glyph, pack_16x16_2bpp
from build_translation_relocation_poc import (
    component_info,
    compose_cluster_payloads,
    read_csv,
    sha256,
    validate_nonoverlapping_writes,
)
from nds_inventory import NdsRom, diff_intervals


FONT_PATHS = ("data/font.bin", "data/font_lc.bin")
EXPECTED_SOURCE_SHA256 = "6C8CB19DE7B86DC5F1147E4927D6916571968A06B5C2E9B88F62C22C271DD0A1"
EXPECTED_PACK_STATUS = "pack_payloads_decoded_nonzero_text_collision_audited"


def functional_directives(text: str) -> list[str]:
    """Protect runtime controls while allowing translator-owned %r wrapping."""
    return [token for token in DIRECTIVE_RE.findall(text) if token.lower() != "%r"]


def is_window_directive(token: str) -> bool:
    return len(token) > 3 and token[0] == "%" and token[1].lower() == "w" and token[2] == "("


def functional_directives_match(source: str, translation: str) -> bool:
    """Allow exact controls plus ``%h%w(same)`` page splits."""
    expected = tuple(functional_directives(source))
    actual = tuple(functional_directives(translation))
    if expected == actual:
        return True

    @lru_cache(maxsize=None)
    def match(source_index: int, translation_index: int, active_window: str) -> bool:
        if source_index == len(expected) and translation_index == len(actual):
            return True
        if (
            translation_index + 1 < len(actual)
            and actual[translation_index].lower() == "%h"
            and is_window_directive(actual[translation_index + 1])
            and actual[translation_index + 1] == active_window
            and match(source_index, translation_index + 2, active_window)
        ):
            return True
        if (
            source_index < len(expected)
            and translation_index < len(actual)
            and expected[source_index] == actual[translation_index]
        ):
            token = actual[translation_index]
            next_window = token if is_window_directive(token) else active_window
            return match(source_index + 1, translation_index + 1, next_window)
        return False

    return match(0, 0, "")


def validate_mapping(
    mapping_data: dict,
    translated_hash: str,
    font_hash: str,
    expected_character_count: int,
) -> dict[str, int]:
    if mapping_data["source_sha256"] != EXPECTED_SOURCE_SHA256:
        raise ValueError("mapping source identity mismatch")
    if mapping_data["translation_ssot_sha256"] != translated_hash:
        raise ValueError("mapping translation SSOT identity mismatch")
    if mapping_data["font_source_sha256"] != font_hash:
        raise ValueError("mapping selected-font identity mismatch")
    rows = mapping_data["mappings"]
    if len(rows) != expected_character_count:
        raise ValueError(
            "full mapping character count differs from the current translation audit: "
            f"mapping={len(rows)}, audit={expected_character_count}"
        )
    if mapping_data["character_count"] != len(rows):
        raise ValueError("mapping character_count does not match its mapping rows")
    characters = [row["character"] for row in rows]
    codes = [int(row["code"], 16) for row in rows]
    if len(set(characters)) != len(characters):
        raise ValueError("duplicate mapping character")
    if len(set(codes)) != len(codes):
        raise ValueError("duplicate mapping code")
    for row, code in zip(rows, codes):
        if sjis_to_index(code) != int(row["glyph_index"]):
            raise ValueError(f"mapping code/index mismatch: {row['character']}")
    return dict(zip(characters, codes))


def build(args: argparse.Namespace) -> dict:
    if args.output_rom.exists() or args.report.exists():
        raise FileExistsError("refusing to overwrite an existing full QA output")

    translated_bytes = args.translated_csv.read_bytes()
    translated_hash = sha256(translated_bytes)
    csv_audit = json.loads(args.translation_audit.read_text(encoding="utf-8"))
    if csv_audit["status"] != "translation_csv_audit_complete":
        raise ValueError("translation CSV audit status mismatch")
    if csv_audit["translated_ssot"]["sha256"] != translated_hash:
        raise ValueError("translation audit SSOT identity mismatch")
    expected_character_count = int(
        csv_audit["glyph_demand"]["unique_hangul_syllable_count"]
    )
    mapping_bytes = args.mapping.read_bytes()
    mapping_data = json.loads(mapping_bytes.decode("utf-8"))
    font_config = validate_selected_font(args.font_png, args.font_config)
    mapping = validate_mapping(
        mapping_data,
        translated_hash,
        font_config["source"]["sha256"],
        expected_character_count,
    )

    pack_bytes = args.pack_audit.read_bytes()
    pack_audit = json.loads(pack_bytes.decode("utf-8"))
    if pack_audit["status"] != EXPECTED_PACK_STATUS:
        raise ValueError("pack audit status mismatch")
    if pack_audit["gates"]["compressed_payload_visibility"] != "pass_arm9_0x02026B9C_decoder_reimplemented":
        raise ValueError("pack compressed-payload gate has not passed")
    if pack_audit["gates"]["decoded_pack_text_collision"] != "pass":
        raise ValueError("pack decoded-text collision gate has not passed")

    capacity = json.loads(args.capacity_audit.read_text(encoding="utf-8"))
    if capacity["translated_ssot"]["sha256"] != translated_hash:
        raise ValueError("capacity audit translation identity mismatch")
    if capacity["gates"]["zero_aligned_cluster_capacity"] != "pass":
        raise ValueError("zero-aligned cluster capacity gate has not passed")

    references_bytes = args.reference_report.read_bytes()
    references = json.loads(references_bytes.decode("utf-8"))
    scripts = json.loads(args.script_report.read_text(encoding="utf-8"))["records"]
    auxiliary = json.loads(args.aux_report.read_text(encoding="utf-8"))["records"]
    source_by_id = {row["text_id"]: row for row in scripts + auxiliary}
    reference_by_id = {row["text_id"]: row for row in references["catalog"]}

    rom = NdsRom(args.source_rom)
    source_hash = sha256(rom.data)
    if source_hash != EXPECTED_SOURCE_SHA256 or references["source_sha256"] != source_hash:
        raise ValueError("source ROM/reference identity mismatch")

    rows = [
        row
        for row in read_csv(args.translated_csv)
        if row["population"] in ACCEPTED_POPULATIONS
    ]
    if len(rows) != 2748:
        raise ValueError(f"unexpected accepted translation row count: {len(rows)}")
    if len({row["text_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate accepted translation text ID")

    payload_by_id: dict[str, bytes] = {}
    components: dict[str, list[dict[str, str]]] = {}
    alias_replacement_count = 0
    for row in rows:
        text_id = row["text_id"]
        source = source_by_id.get(text_id)
        reference = reference_by_id.get(text_id)
        if source is None or reference is None:
            raise ValueError(f"source/reference record missing: {text_id}")
        if bytes.fromhex(row["source_raw_hex"]) != bytes.fromhex(source["raw_hex"]):
            raise ValueError(f"protected source raw mismatch: {text_id}")
        if row["source_text"] != source["cp932"]:
            raise ValueError(f"protected source text mismatch: {text_id}")
        if not row["translation_text"].strip():
            raise ValueError(f"accepted translation is blank: {text_id}")
        if not functional_directives_match(row["source_text"], row["translation_text"]):
            raise ValueError(f"directive sequence mismatch: {text_id}")
        try:
            alias_replacement_count += sum(
                row["translation_text"].count(character)
                for character in ENCODING_ALIAS_CANDIDATES
            )
            payload_by_id[text_id] = encode_literal(
                apply_alias_candidates(row["translation_text"]), mapping
            )
        except UnicodeEncodeError as error:
            raise ValueError(f"unmapped translation character: {text_id}: {error}") from error
        components.setdefault(row["component"], []).append(row)

    output = bytearray(rom.data)
    writes: list[dict] = []
    component_reports: list[dict] = []
    total_pointer_writes = 0
    total_borrowed_zero_bytes = 0
    total_clusters = 0

    for component, component_rows in sorted(components.items()):
        component_start, component_size, component_load = component_info(rom, component)
        component_data = rom.data[component_start : component_start + component_size]
        intervals: list[tuple[int, int, str]] = []
        pointer_ranges: list[tuple[int, int]] = []
        for row in component_rows:
            text_id = row["text_id"]
            offset = int(row["component_offset_hex"], 16)
            raw = bytes.fromhex(row["source_raw_hex"])
            end = offset + len(raw)
            if component_data[offset:end] != raw:
                raise ValueError(f"component Expected Write mismatch: {text_id}")
            if end >= component_size or component_data[end] != 0:
                raise ValueError(f"source NUL boundary mismatch: {text_id}")
            intervals.append((offset, end + 1, text_id))
            reference = reference_by_id[text_id]
            if reference["pointer_encoding"] != "u32le_absolute_ram":
                raise ValueError(f"unsupported pointer encoding: {text_id}")
            expected_pointer = (component_load + offset).to_bytes(4, "little")
            if bytes.fromhex(reference["pointer_raw_hex"]) != expected_pointer:
                raise ValueError(f"reference target mismatch: {text_id}")
            for pointer_offset in reference["reference_offsets"]:
                if component_data[pointer_offset : pointer_offset + 4] != expected_pointer:
                    raise ValueError(f"stored pointer mismatch: {text_id}@0x{pointer_offset:X}")
                pointer_ranges.append((pointer_offset, pointer_offset + 4))

        regions, borrowed_zero_bytes = merge_zero_aligned_slots(
            component_data, intervals, pointer_ranges, max_gap=3
        )
        placements, failures = pack_records_into_regions(
            [
                {
                    "text_id": row["text_id"],
                    "translation_length": len(payload_by_id[row["text_id"]]),
                }
                for row in component_rows
            ],
            regions,
        )
        if failures:
            raise ValueError(f"component text packing failed: {component}: {failures}")
        placement_by_id = {row["translation_text_id"]: row for row in placements}
        cluster_payloads = compose_cluster_payloads(
            regions,
            placements,
            {row["text_id"]: payload_by_id[row["text_id"]] for row in component_rows},
        )

        component_write_count_before = len(writes)
        for cluster in cluster_payloads:
            rom_offset = component_start + cluster["start"]
            replacement = cluster["payload"]
            expected = rom.data[rom_offset : rom_offset + len(replacement)]
            if expected != replacement:
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
                output[rom_offset : rom_offset + len(replacement)] = replacement

        pointer_write_count = 0
        for row in component_rows:
            text_id = row["text_id"]
            old_offset = int(row["component_offset_hex"], 16)
            new_offset = placement_by_id[text_id]["target_offset"]
            expected_pointer = (component_load + old_offset).to_bytes(4, "little")
            replacement_pointer = (component_load + new_offset).to_bytes(4, "little")
            for pointer_offset in reference_by_id[text_id]["reference_offsets"]:
                if expected_pointer == replacement_pointer:
                    continue
                rom_offset = component_start + pointer_offset
                if rom.data[rom_offset : rom_offset + 4] != expected_pointer:
                    raise ValueError(f"pointer Expected Write mismatch: {text_id}@0x{pointer_offset:X}")
                writes.append(
                    {
                        "owner": "relocated_text_pointer",
                        "component": component,
                        "text_id": text_id,
                        "component_offset": pointer_offset,
                        "rom_offset": rom_offset,
                        "size": 4,
                        "expected_hex": expected_pointer.hex().upper(),
                        "replacement_hex": replacement_pointer.hex().upper(),
                        "old_target_offset": old_offset,
                        "new_target_offset": new_offset,
                    }
                )
                output[rom_offset : rom_offset + 4] = replacement_pointer
                pointer_write_count += 1

        output_component = output[component_start : component_start + component_size]
        for row in component_rows:
            text_id = row["text_id"]
            new_offset = placement_by_id[text_id]["target_offset"]
            payload = payload_by_id[text_id]
            if output_component[new_offset : new_offset + len(payload)] != payload:
                raise AssertionError(f"relocated payload readback mismatch: {text_id}")
            if output_component[new_offset + len(payload)] != 0:
                raise AssertionError(f"relocated payload NUL mismatch: {text_id}")
            expected_pointer = (component_load + new_offset).to_bytes(4, "little")
            for pointer_offset in reference_by_id[text_id]["reference_offsets"]:
                if output_component[pointer_offset : pointer_offset + 4] != expected_pointer:
                    raise AssertionError(f"relocated pointer readback mismatch: {text_id}")

        total_pointer_writes += pointer_write_count
        total_borrowed_zero_bytes += borrowed_zero_bytes
        total_clusters += len(regions)
        component_reports.append(
            {
                "component": component,
                "record_count": len(component_rows),
                "cluster_count": len(regions),
                "borrowed_zero_alignment_bytes": borrowed_zero_bytes,
                "pointer_write_count": pointer_write_count,
                "registered_write_count": len(writes) - component_write_count_before,
            }
        )

    override_bytes = args.runtime_label_overrides.read_bytes()
    override_data = json.loads(override_bytes.decode("utf-8"))
    if override_data.get("source_rom_sha256") != source_hash:
        raise ValueError("runtime-label override source identity mismatch")
    override_reports: list[dict] = []
    for entry in override_data.get("entries", []):
        component = entry["component"]
        component_start, component_size, _ = component_info(rom, component)
        component_offset = int(entry["component_offset"], 0)
        source_raw = bytes.fromhex(entry["source_raw_hex"])
        source_with_nul = source_raw + b"\0"
        if component_offset + len(source_with_nul) > component_size:
            raise ValueError(f"runtime-label override exceeds component: {entry['text_id']}")
        rom_offset = component_start + component_offset
        expected = rom.data[rom_offset:rom_offset + len(source_with_nul)]
        if expected != source_with_nul:
            raise ValueError(f"runtime-label Expected Write mismatch: {entry['text_id']}")
        replacement_payload = encode_literal(entry["translation_text"], mapping) + b"\0"
        if len(replacement_payload) > len(source_with_nul):
            raise ValueError(f"runtime-label replacement exceeds slot: {entry['text_id']}")
        replacement = replacement_payload.ljust(len(source_with_nul), b"\0")
        writes.append(
            {
                "owner": "runtime_label_override",
                "component": component,
                "text_id": entry["text_id"],
                "component_offset": component_offset,
                "rom_offset": rom_offset,
                "size": len(replacement),
                "expected_sha256": sha256(expected),
                "replacement_sha256": sha256(replacement),
                "source_text": entry["source_text"],
                "translation_text": entry["translation_text"],
                "evidence": entry["evidence"],
            }
        )
        output[rom_offset:rom_offset + len(replacement)] = replacement
        if output[rom_offset:rom_offset + len(replacement_payload)] != replacement_payload:
            raise AssertionError(f"runtime-label readback mismatch: {entry['text_id']}")
        override_reports.append(
            {
                "text_id": entry["text_id"],
                "component": component,
                "component_offset": entry["component_offset"],
                "source_text": entry["source_text"],
                "translation_text": entry["translation_text"],
                "slot_size": len(source_with_nul),
                "replacement_size": len(replacement_payload),
                "status": entry["status"],
            }
        )

    glyph_cache = {
        row["character"]: pack_16x16_2bpp(load_target_glyph(args.font_png, row["character"]))
        for row in mapping_data["mappings"]
    }
    font_reports: list[dict] = []
    for font_path in FONT_PATHS:
        file_id = next((file_id for file_id, path in rom.paths.items() if path == font_path), None)
        if file_id is None:
            raise ValueError(f"NitroFS font not found: {font_path}")
        font_start, font_end = rom.fat[file_id]
        changed_glyph_count = 0
        for row in mapping_data["mappings"]:
            index = int(row["glyph_index"])
            rom_offset = font_start + index * GLYPH_SIZE
            if rom_offset + GLYPH_SIZE > font_end:
                raise ValueError(f"font glyph write exceeds {font_path}")
            expected = rom.data[rom_offset : rom_offset + GLYPH_SIZE]
            replacement = glyph_cache[row["character"]]
            if expected == replacement:
                continue
            writes.append(
                {
                    "owner": "hangul_glyph",
                    "component": font_path,
                    "character": row["character"],
                    "sjis_code": row["code"],
                    "glyph_index": index,
                    "rom_offset": rom_offset,
                    "size": GLYPH_SIZE,
                    "expected_sha256": sha256(expected),
                    "replacement_sha256": sha256(replacement),
                }
            )
            output[rom_offset : rom_offset + GLYPH_SIZE] = replacement
            changed_glyph_count += 1
        font_reports.append(
            {
                "path": font_path,
                "rom_offset": font_start,
                "size": font_end - font_start,
                "selected_glyph_count": len(mapping_data["mappings"]),
                "changed_glyph_count": changed_glyph_count,
            }
        )

    validate_nonoverlapping_writes(writes)
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
    changed_count, changed_intervals = diff_intervals(rom.data, output_bytes)
    if changed_count != len(changed_offsets):
        raise AssertionError("diff count disagreement")

    report = {
        "schema_version": 1,
        "build_id": "another-code-full-translation-dual-font-qa-v1",
        "status": "static_build_pass_runtime_whole_game_pending",
        "build_class": "whole_catalog_qa_candidate_not_release",
        "source": {"path": str(args.source_rom), "sha256": source_hash, "size": len(rom.data)},
        "translated_ssot": {
            "path": str(args.translated_csv),
            "sha256": translated_hash,
            "modified_by_build": False,
        },
        "runtime_label_overrides": {
            "path": str(args.runtime_label_overrides),
            "sha256": sha256(override_bytes),
            "entry_count": len(override_reports),
            "entries": override_reports,
        },
        "mapping": {
            "path": str(args.mapping),
            "sha256": sha256(mapping_bytes),
            "mapping_id": mapping_data["mapping_id"],
            "character_count": len(mapping_data["mappings"]),
        },
        "pack_audit": {
            "path": str(args.pack_audit),
            "sha256": sha256(pack_bytes),
            "status": pack_audit["status"],
        },
        "scope": {
            "accepted_populations": sorted(ACCEPTED_POPULATIONS),
            "component_count": len(components),
            "record_count": len(rows),
            "cluster_count": total_clusters,
            "borrowed_zero_alignment_bytes": total_borrowed_zero_bytes,
            "pointer_write_count": total_pointer_writes,
            "font_paths": list(FONT_PATHS),
            "build_layer_encoding_aliases": ENCODING_ALIAS_CANDIDATES,
            "encoding_alias_replacement_count": alias_replacement_count,
        },
        "components": component_reports,
        "fonts": font_reports,
        "output": {
            "path": str(args.output_rom),
            "sha256": sha256(output_bytes),
            "size": len(output_bytes),
        },
        "changed_byte_count": changed_count,
        "changed_intervals": changed_intervals,
        "registered_write_count": len(writes),
        "registered_write_counts": dict(sorted(Counter(row["owner"] for row in writes).items())),
        "writes": writes,
        "static_gates": {
            "source_identity": "pass",
            "final_csv_identity_and_protected_fields": "pass",
            "directive_sequence_and_arguments": "pass",
            "mapping_translation_derived_unique_code_index_pairs": "pass",
            "pack_compressed_payload_visibility": "pass",
            "pack_strong_text_collision": "pass",
            "all_accepted_records_cluster_capacity": "pass",
            "all_cataloged_direct_pointers_readback": "pass",
            "dual_main_font_glyph_readback": "pass",
            "rom_size_and_registered_write_boundary": "pass",
        },
        "runtime_gates": {
            "clean_boot": "pending",
            "intro_and_dialogue": "pending",
            "percent_u_font_switch_paths": "pending",
            "auxiliary_ui_and_names": "pending",
            "save_load_and_progression": "pending",
            "whole_game": "pending",
        },
        "not_proved": [
            "runtime behavior of all 2748 relocated records",
            "layout quality, wrapping, and clipping across all scenes",
            "db_font.bin consumer coverage",
            "whole-game progression and save compatibility",
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
    parser.add_argument("--translated-csv", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--font-png", type=Path, required=True)
    parser.add_argument("--font-config", type=Path, required=True)
    parser.add_argument("--script-report", type=Path, required=True)
    parser.add_argument("--aux-report", type=Path, required=True)
    parser.add_argument("--reference-report", type=Path, required=True)
    parser.add_argument("--translation-audit", type=Path, required=True)
    parser.add_argument("--capacity-audit", type=Path, required=True)
    parser.add_argument("--pack-audit", type=Path, required=True)
    parser.add_argument("--runtime-label-overrides", type=Path, required=True)
    parser.add_argument("--output-rom", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    build(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
