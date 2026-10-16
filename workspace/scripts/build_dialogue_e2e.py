#!/usr/bin/env python3
"""Build a fixed-length representative Korean dialogue E2E ROM."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from analyze_game_font import GLYPH_SIZE, sjis_to_index
from build_font_poc import load_target_glyph, pack_16x16_2bpp
from nds_inventory import NdsRom


FONT_PATH = "data/font_lc.bin"
DIRECTIVE_RE = re.compile(r"%[A-Za-z](?:\([^)]*\))?")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def encode_literal(text: str, mapping: dict[str, int]) -> bytes:
    output = bytearray()
    for char in text:
        if char in mapping:
            output.extend(mapping[char].to_bytes(2, "big"))
        else:
            output.extend(char.encode("cp932"))
    return bytes(output)


def validate_selected_font(font_path: Path, font_config_path: Path) -> dict:
    font_config = json.loads(font_config_path.read_text(encoding="utf-8"))
    if font_config.get("selection_status") != "approved_by_user_runtime_qa":
        raise ValueError("font config does not contain an approved runtime selection")
    actual_hash = sha256(font_path.read_bytes())
    expected_hash = font_config["source"]["sha256"]
    if actual_hash != expected_hash:
        raise ValueError(
            f"selected font SHA-256 mismatch: expected {expected_hash}, got {actual_hash}"
        )
    return font_config


def build(args: argparse.Namespace) -> None:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    translation = json.loads(args.translation.read_text(encoding="utf-8"))
    font_config = validate_selected_font(args.font_png, args.font_config)
    scripts = json.loads(args.script_report.read_text(encoding="utf-8"))["records"]
    auxiliary = json.loads(args.aux_report.read_text(encoding="utf-8"))["records"]
    references = json.loads(args.reference_report.read_text(encoding="utf-8"))["catalog"]
    rom = NdsRom(args.source_rom)
    source_hash = sha256(rom.data)
    if source_hash != config["source_sha256"]:
        raise ValueError(f"source SHA-256 mismatch: {source_hash}")
    if translation["translation_id"] != config["build_id"]:
        raise ValueError("translation/config identity mismatch")
    if translation["text_id"] != config["target_text_id"]:
        raise ValueError("translation/config target mismatch")

    target = next((row for row in scripts if row["text_id"] == config["target_text_id"]), None)
    if target is None:
        raise ValueError("target text record not found")
    reference = next(
        (row for row in references if row["text_id"] == target["text_id"]), None
    )
    if reference is None or reference["reference_count"] != 1:
        raise ValueError("representative target must have exactly one cataloged direct reference")

    mappings = {row["character"]: int(row["code"], 16) for row in config["mappings"]}
    if len(mappings) != len(config["mappings"]):
        raise ValueError("duplicate mapping character")
    if len(set(mappings.values())) != len(mappings):
        raise ValueError("duplicate mapping code")
    for row in config["mappings"]:
        code = int(row["code"], 16)
        if sjis_to_index(code) != row["glyph_index"]:
            raise ValueError(f"glyph index mismatch for {row['character']}")
        needle = code.to_bytes(2, "big")
        if any(needle in bytes.fromhex(item["raw_hex"]) for item in scripts + auxiliary):
            raise ValueError(f"mapping code already used by the cataloged source population: {code:04X}")

    source_literal = config["source_literal"].encode("cp932")
    translated_literal = encode_literal(config["translation_literal"], mappings)
    if translation["source_literal"] != config["source_literal"]:
        raise ValueError("translation source literal mismatch")
    if translation["translation_literal"] != config["translation_literal"]:
        raise ValueError("translation literal mismatch")
    if len(source_literal) != len(translated_literal):
        raise ValueError("E2E translation must preserve literal byte length")
    if DIRECTIVE_RE.findall(config["source_literal"]) != DIRECTIVE_RE.findall(
        config["translation_literal"]
    ):
        raise ValueError("E2E translation must preserve directive sequence")
    record_raw = bytes.fromhex(target["raw_hex"])
    literal_offset = record_raw.find(source_literal)
    if literal_offset < 0 or record_raw.find(source_literal, literal_offset + 1) >= 0:
        raise ValueError("source literal must occur exactly once in target record")

    component = target["component"]
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
        raise ValueError("target overlay not found")
    overlay_start, overlay_end = rom.fat[overlay["file_id"]]
    text_component_offset = target["offset"] + literal_offset
    text_rom_offset = overlay_start + text_component_offset
    if bytes(rom.data[text_rom_offset : text_rom_offset + len(source_literal)]) != source_literal:
        raise ValueError("target literal Expected Write mismatch")

    output = bytearray(rom.data)
    output[text_rom_offset : text_rom_offset + len(source_literal)] = translated_literal
    writes = [
        {
            "owner": "dialogue_e2e_literal",
            "text_id": target["text_id"],
            "component": component,
            "component_offset": text_component_offset,
            "rom_offset": text_rom_offset,
            "size": len(source_literal),
            "expected_hex": source_literal.hex().upper(),
            "replacement_hex": translated_literal.hex().upper(),
        }
    ]

    font_file_id = next(
        (file_id for file_id, path in rom.paths.items() if path == FONT_PATH), None
    )
    if font_file_id is None:
        raise ValueError("font_lc.bin not found")
    font_start, font_end = rom.fat[font_file_id]
    for row in config["mappings"]:
        char = row["character"]
        code = int(row["code"], 16)
        glyph = pack_16x16_2bpp(load_target_glyph(args.font_png, char))
        glyph_offset = font_start + row["glyph_index"] * GLYPH_SIZE
        if glyph_offset + GLYPH_SIZE > font_end:
            raise ValueError("glyph write exceeds font_lc.bin")
        original = bytes(output[glyph_offset : glyph_offset + GLYPH_SIZE])
        output[glyph_offset : glyph_offset + GLYPH_SIZE] = glyph
        writes.append(
            {
                "owner": "dialogue_e2e_font",
                "component": FONT_PATH,
                "file_id": font_file_id,
                "character": char,
                "sjis_code": f"{code:04X}",
                "glyph_index": row["glyph_index"],
                "component_offset": row["glyph_index"] * GLYPH_SIZE,
                "rom_offset": glyph_offset,
                "size": GLYPH_SIZE,
                "expected_sha256": sha256(original),
                "replacement_sha256": sha256(glyph),
            }
        )

    output_bytes = bytes(output)
    allowed = set(range(text_rom_offset, text_rom_offset + len(source_literal)))
    for row in writes[1:]:
        allowed.update(range(row["rom_offset"], row["rom_offset"] + row["size"]))
    changed = [
        index for index, pair in enumerate(zip(rom.data, output_bytes)) if pair[0] != pair[1]
    ]
    if any(offset not in allowed for offset in changed):
        raise AssertionError("unregistered output difference")
    args.output_rom.parent.mkdir(parents=True, exist_ok=True)
    args.output_rom.write_bytes(output_bytes)
    report = {
        "schema_version": 1,
        "gate_id": config["build_id"],
        "status": "static_build_pass_runtime_pending",
        "source": {"path": str(args.source_rom), "sha256": source_hash, "size": len(rom.data)},
        "output": {
            "path": str(args.output_rom),
            "sha256": sha256(output_bytes),
            "size": len(output_bytes),
        },
        "font_source": {
            "path": str(args.font_png),
            "sha256": font_config["source"]["sha256"],
            "selection_status": font_config["selection_status"],
        },
        "text_id": target["text_id"],
        "source_literal": config["source_literal"],
        "expected_screen_text": config.get(
            "expected_screen_text", config["translation_literal"]
        ),
        "source_literal_bytes": len(source_literal),
        "translation_literal_bytes": len(translated_literal),
        "record_bytes_preserved": len(record_raw),
        "direct_reference_offsets": reference["reference_offsets"],
        "pointer_target_preserved": True,
        "directive_bytes_preserved": True,
        "changed_byte_count": len(changed),
        "writes": writes,
        "not_proved": [
            f"runtime display of all {len(mappings)} mapped Hangul glyphs",
            "final Korean punctuation and translation wording",
            "record growth or relocation",
            "whole-game mapping capacity and renderer coverage",
        ],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-rom", type=Path, required=True)
    parser.add_argument("--font-png", type=Path, required=True)
    parser.add_argument("--font-config", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--translation", type=Path, required=True)
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
