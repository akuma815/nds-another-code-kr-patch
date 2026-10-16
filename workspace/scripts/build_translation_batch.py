#!/usr/bin/env python3
"""Build a fixed-length multi-record Korean development PoC ROM."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from analyze_game_font import GLYPH_SIZE, sjis_to_index
from build_dialogue_e2e import DIRECTIVE_RE, encode_literal, validate_selected_font
from build_font_poc import load_target_glyph, pack_16x16_2bpp
from nds_inventory import NdsRom


FONT_PATH = "data/font_lc.bin"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def validate_entry(source: dict, entry: dict, mapping: dict[str, int]) -> bytes:
    if source["text_id"] != entry["text_id"]:
        raise ValueError("source/translation text ID mismatch")
    expected_raw = bytes.fromhex(entry["source_raw_hex"])
    if bytes.fromhex(source["raw_hex"]) != expected_raw:
        raise ValueError(f"protected source raw mismatch: {entry['text_id']}")
    if source["cp932"] != entry["source_text"]:
        raise ValueError(f"protected source text mismatch: {entry['text_id']}")
    source_tokens = DIRECTIVE_RE.findall(entry["source_text"])
    translated_tokens = DIRECTIVE_RE.findall(entry["translation_text"])
    if source_tokens != translated_tokens:
        raise ValueError(f"directive sequence mismatch: {entry['text_id']}")
    try:
        translated = encode_literal(entry["translation_text"], mapping)
    except UnicodeEncodeError as exc:
        raise ValueError(f"unmapped translation character in {entry['text_id']}: {exc}") from exc
    if len(translated) != len(expected_raw):
        raise ValueError(
            f"fixed-length overflow in {entry['text_id']}: "
            f"source {len(expected_raw)}, translation {len(translated)}"
        )
    return translated


def build(args: argparse.Namespace) -> None:
    batch = json.loads(args.batch.read_text(encoding="utf-8"))
    mapping_data = json.loads(args.mapping.read_text(encoding="utf-8"))
    font_config = validate_selected_font(args.font_png, args.font_config)
    scripts = json.loads(args.script_report.read_text(encoding="utf-8"))["records"]
    auxiliary = json.loads(args.aux_report.read_text(encoding="utf-8"))["records"]
    references = json.loads(args.reference_report.read_text(encoding="utf-8"))["catalog"]
    source_by_id = {row["text_id"]: row for row in scripts}
    reference_by_id = {row["text_id"]: row for row in references}
    rom = NdsRom(args.source_rom)
    source_hash = sha256(rom.data)
    if source_hash != batch["source_sha256"] or source_hash != mapping_data["source_sha256"]:
        raise ValueError("source ROM identity mismatch")
    if mapping_data["batch_id"] != batch["batch_id"]:
        raise ValueError("mapping/batch identity mismatch")
    if mapping_data["font_sha256"] != font_config["source"]["sha256"]:
        raise ValueError("mapping/font identity mismatch")

    mapping = {row["character"]: int(row["code"], 16) for row in mapping_data["mappings"]}
    if len(mapping) != len(mapping_data["mappings"]):
        raise ValueError("duplicate mapping character")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("duplicate mapping code")
    source_windows = {
        raw[offset : offset + 2]
        for row in scripts + auxiliary
        for raw in [bytes.fromhex(row["raw_hex"])]
        for offset in range(max(0, len(raw) - 1))
    }
    for row in mapping_data["mappings"]:
        code = int(row["code"], 16)
        if sjis_to_index(code) != row["glyph_index"]:
            raise ValueError(f"mapping glyph index mismatch: {row['character']}")
        if code.to_bytes(2, "big") in source_windows:
            raise ValueError(f"mapping code is present in source catalog: {code:04X}")

    overlay_by_component = {
        f"arm9_overlay_{row['overlay_id']:03d}_file_{row['file_id']:03d}": row
        for row in rom.arm9_overlays
    }
    output = bytearray(rom.data)
    writes: list[dict] = []
    entry_reports: list[dict] = []
    seen_ids: set[str] = set()
    for entry in batch["entries"]:
        text_id = entry["text_id"]
        if text_id in seen_ids:
            raise ValueError(f"duplicate batch text ID: {text_id}")
        seen_ids.add(text_id)
        source = source_by_id.get(text_id)
        reference = reference_by_id.get(text_id)
        if source is None or reference is None or reference["reference_count"] < 1:
            raise ValueError(f"missing source/reference ownership: {text_id}")
        replacement = validate_entry(source, entry, mapping)
        overlay = overlay_by_component.get(source["component"])
        if overlay is None:
            raise ValueError(f"unsupported batch component: {source['component']}")
        overlay_start, _overlay_end = rom.fat[overlay["file_id"]]
        rom_offset = overlay_start + source["offset"]
        expected = bytes.fromhex(entry["source_raw_hex"])
        if bytes(output[rom_offset : rom_offset + len(expected)]) != expected:
            raise ValueError(f"record Expected Write mismatch: {text_id}")
        output[rom_offset : rom_offset + len(expected)] = replacement
        writes.append(
            {
                "owner": "translation_record",
                "text_id": text_id,
                "component": source["component"],
                "component_offset": source["offset"],
                "rom_offset": rom_offset,
                "size": len(expected),
                "expected_sha256": sha256(expected),
                "replacement_sha256": sha256(replacement),
            }
        )
        entry_reports.append(
            {
                "text_id": text_id,
                "status": entry["status"],
                "context_status": entry["context_status"],
                "source_bytes": len(expected),
                "translation_bytes": len(replacement),
                "direct_reference_offsets": reference["reference_offsets"],
                "directives": DIRECTIVE_RE.findall(entry["source_text"]),
                "pointer_target_preserved": True,
            }
        )

    font_file_id = next((fid for fid, path in rom.paths.items() if path == FONT_PATH), None)
    if font_file_id is None:
        raise ValueError("font_lc.bin not found")
    font_start, font_end = rom.fat[font_file_id]
    for row in mapping_data["mappings"]:
        glyph = pack_16x16_2bpp(load_target_glyph(args.font_png, row["character"]))
        glyph_offset = font_start + row["glyph_index"] * GLYPH_SIZE
        if glyph_offset + GLYPH_SIZE > font_end:
            raise ValueError("glyph write exceeds font_lc.bin")
        original = bytes(output[glyph_offset : glyph_offset + GLYPH_SIZE])
        output[glyph_offset : glyph_offset + GLYPH_SIZE] = glyph
        writes.append(
            {
                "owner": "hangul_glyph",
                "character": row["character"],
                "sjis_code": row["code"],
                "glyph_index": row["glyph_index"],
                "component": FONT_PATH,
                "rom_offset": glyph_offset,
                "size": GLYPH_SIZE,
                "expected_sha256": sha256(original),
                "replacement_sha256": sha256(glyph),
            }
        )

    output_bytes = bytes(output)
    allowed: set[int] = set()
    for write in writes:
        allowed.update(range(write["rom_offset"], write["rom_offset"] + write["size"]))
    changed = [
        offset
        for offset, (left, right) in enumerate(zip(rom.data, output_bytes))
        if left != right
    ]
    if any(offset not in allowed for offset in changed):
        raise AssertionError("unregistered output difference")
    args.output_rom.parent.mkdir(parents=True, exist_ok=True)
    args.output_rom.write_bytes(output_bytes)
    report = {
        "schema_version": 1,
        "gate_id": batch["batch_id"],
        "status": "static_build_pass_runtime_pending",
        "build_class": batch["build_class"],
        "source": {"path": str(args.source_rom), "sha256": source_hash, "size": len(rom.data)},
        "font_source": {
            "path": str(args.font_png),
            "sha256": font_config["source"]["sha256"],
            "selection_status": font_config["selection_status"],
        },
        "mapping": {
            "path": str(args.mapping),
            "mapping_id": mapping_data["mapping_id"],
            "character_count": len(mapping_data["mappings"]),
        },
        "output": {
            "path": str(args.output_rom),
            "sha256": sha256(output_bytes),
            "size": len(output_bytes),
        },
        "entry_count": len(entry_reports),
        "entries": entry_reports,
        "changed_byte_count": len(changed),
        "writes": writes,
        "preserved": [
            "record lengths and NUL boundaries",
            "directive byte sequences and order",
            "all cataloged direct pointer targets",
            "ROM size and all unregistered bytes",
        ],
        "not_proved": [
            "runtime order of the second record",
            "final translation quality or approval",
            "record growth or relocation",
            "whole-game safe code pool and glyph coverage",
        ],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-rom", type=Path, required=True)
    parser.add_argument("--font-png", type=Path, required=True)
    parser.add_argument("--font-config", type=Path, required=True)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--script-report", type=Path, required=True)
    parser.add_argument("--aux-report", type=Path, required=True)
    parser.add_argument("--reference-report", type=Path, required=True)
    parser.add_argument("--output-rom", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    build(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
