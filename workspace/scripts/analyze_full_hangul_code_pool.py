#!/usr/bin/env python3
"""Audit the complete fixed-font code domain against the final translation SSOT.

This report deliberately separates parser-reachable text use from incidental
two-byte windows in the whole ROM.  A binary window hit is a risk signal, not
proof that the text consumer can reach that byte pair.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

from analyze_game_font import GLYPH_SIZE, index_to_sjis, sjis_to_index
from analyze_translation_relocation import apply_alias_candidates
from audit_translation_csv import literal_text


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def code_index_or_none(lead: int, trail: int, glyph_count: int) -> int | None:
    if not 0x81 <= lead <= 0x98:
        return None
    if not 0x40 <= trail <= 0xFC or trail == 0x7F:
        return None
    trail_index = trail - 0x40 - (1 if trail >= 0x80 else 0)
    index = (lead - 0x81) * 188 + trail_index
    return index if index < glyph_count else None


def parsed_code_indices(data: bytes, glyph_count: int) -> list[int]:
    """Parse game-font pairs at Shift-JIS character boundaries."""
    result: list[int] = []
    offset = 0
    while offset < len(data):
        if offset + 1 < len(data):
            index = code_index_or_none(data[offset], data[offset + 1], glyph_count)
            if index is not None:
                result.append(index)
                offset += 2
                continue
        offset += 1
    return result


def window_code_indices(data: bytes, glyph_count: int) -> set[int]:
    result: set[int] = set()
    for offset in range(len(data) - 1):
        index = code_index_or_none(data[offset], data[offset + 1], glyph_count)
        if index is not None:
            result.add(index)
    return result


def whole_rom_window_counts(data: bytes, glyph_count: int) -> list[int]:
    counts = [0] * glyph_count
    for offset in range(len(data) - 1):
        index = code_index_or_none(data[offset], data[offset + 1], glyph_count)
        if index is not None:
            counts[index] += 1
    return counts


def compact_ranges(indices: list[int]) -> list[dict[str, int | str]]:
    if not indices:
        return []
    values = sorted(indices)
    result: list[dict[str, int | str]] = []
    start = previous = values[0]
    for value in values[1:]:
        if value != previous + 1:
            result.append(range_row(start, previous))
            start = value
        previous = value
    result.append(range_row(start, previous))
    return result


def range_row(start: int, end: int) -> dict[str, int | str]:
    return {
        "start_glyph_index": start,
        "end_glyph_index": end,
        "count": end - start + 1,
        "start_code": f"{index_to_sjis(start):04X}",
        "end_code": f"{index_to_sjis(end):04X}",
    }


def cp932_status(code: int) -> str:
    try:
        code.to_bytes(2, "big").decode("cp932")
    except UnicodeDecodeError:
        return "undefined_in_cp932"
    return "defined_in_cp932"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def translation_demand(rows: list[dict[str, str]]) -> tuple[list[str], set[int]]:
    hangul: list[str] = []
    seen_hangul: set[str] = set()
    nonhangul_codes: set[int] = set()
    for row in rows:
        text = row.get("translation_text", "")
        if not text:
            continue
        for char in literal_text(apply_alias_candidates(text)):
            if "가" <= char <= "힣":
                if char not in seen_hangul:
                    seen_hangul.add(char)
                    hangul.append(char)
                continue
            encoded = char.encode("cp932")
            if len(encoded) == 2:
                nonhangul_codes.add(int.from_bytes(encoded, "big"))
    return hangul, nonhangul_codes


def audit(args: argparse.Namespace) -> dict:
    rom = args.rom.read_bytes()
    font = args.font.read_bytes()
    csv_bytes = args.translation_csv.read_bytes()
    rows = read_csv(args.translation_csv)
    if len(font) % GLYPH_SIZE:
        raise ValueError("font size is not divisible by 64 bytes per glyph")
    glyph_count = len(font) // GLYPH_SIZE
    if glyph_count != 4375:
        raise ValueError(f"unexpected font glyph count: {glyph_count}")

    # Independently exercise every index boundary instead of trusting one
    # generator/checker sample to validate the shared conversion formula.
    generated_codes: list[int] = []
    for index in range(glyph_count):
        code = index_to_sjis(index)
        if code & 0xFF == 0x7F:
            raise ValueError(f"forbidden trail generated at index {index}")
        if sjis_to_index(code) != index:
            raise ValueError(f"code/index roundtrip failed at index {index}")
        generated_codes.append(code)
    if len(set(generated_codes)) != glyph_count:
        raise ValueError("code/index mapping is not one-to-one")

    hangul, translation_nonhangul_codes = translation_demand(rows)
    translation_nonhangul_indices = {
        sjis_to_index(code)
        for code in translation_nonhangul_codes
        if sjis_to_index(code) < glyph_count
    }
    source_parsed: Counter[int] = Counter()
    source_windows: set[int] = set()
    raw_rows = 0
    for row in rows:
        raw_hex = row.get("source_raw_hex", "")
        if not raw_hex:
            continue
        raw = bytes.fromhex(raw_hex)
        raw_rows += 1
        source_parsed.update(parsed_code_indices(raw, glyph_count))
        source_windows.update(window_code_indices(raw, glyph_count))

    glyphs = [font[i * GLYPH_SIZE : (i + 1) * GLYPH_SIZE] for i in range(glyph_count)]
    zero_indices = {i for i, glyph in enumerate(glyphs) if not any(glyph)}
    uniform_nonzero_indices = {
        i for i, glyph in enumerate(glyphs) if glyph and len(set(glyph)) == 1 and glyph[0] != 0
    }
    whole_rom_counts = whole_rom_window_counts(rom, glyph_count)

    # Tier A is intentionally strict within the known text population: the code
    # does not occur in any adjacent two-byte window of any exported source row.
    # Original blank/nonblank state is reported separately because this font has
    # fewer blank slots than the final Hangul demand.  Replacing a nonblank slot
    # remains gated on other-consumer/reference proof.
    tier_a = sorted(
        set(range(glyph_count)) - source_windows - translation_nonhangul_indices,
        reverse=True,
    )
    tier_a_zero = sorted(set(tier_a) & zero_indices, reverse=True)
    tier_a_nonzero = sorted(set(tier_a) - zero_indices, reverse=True)
    tier_b = sorted(
        set(range(glyph_count))
        - set(source_parsed)
        - translation_nonhangul_indices
        - set(tier_a),
        reverse=True,
    )

    existing_mapping = json.loads(args.existing_mapping.read_text(encoding="utf-8"))
    existing_indices = [row["glyph_index"] for row in existing_mapping["mappings"]]
    existing_outside_tier_a = sorted(set(existing_indices) - set(tier_a))
    if existing_outside_tier_a:
        raise ValueError(
            "runtime-proven PoC mapping is outside Tier A: "
            + ", ".join(map(str, existing_outside_tier_a))
        )
    if len(tier_a) < len(hangul):
        selected: list[int] = []
    else:
        selected = list(
            dict.fromkeys(existing_indices + tier_a_zero + tier_a_nonzero)
        )[: len(hangul)]
    selected_rows = [
        {
            "glyph_index": index,
            "code": f"{index_to_sjis(index):04X}",
            "cp932_status": cp932_status(index_to_sjis(index)),
            "original_glyph": "all_zero" if index in zero_indices else "nonzero",
            "whole_rom_adjacent_window_count": whole_rom_counts[index],
            "runtime_evidence": (
                "selected_intro_three_screen_pass"
                if index in existing_indices
                else "not_runtime_tested"
            ),
        }
        for index in selected
    ]

    report = {
        "schema_version": 1,
        "status": (
            "static_capacity_pass_runtime_range_gate_pending"
            if len(selected) == len(hangul)
            else "static_capacity_fail"
        ),
        "baseline": {
            "rom": {
                "path": str(args.rom),
                "size": len(rom),
                "sha256": sha256(rom),
            },
            "font": {
                "path": str(args.font),
                "size": len(font),
                "sha256": sha256(font),
            },
            "translation_ssot": {
                "path": str(args.translation_csv),
                "size": len(csv_bytes),
                "sha256": sha256(csv_bytes),
                "row_count": len(rows),
            },
            "existing_runtime_proven_mapping": {
                "path": str(args.existing_mapping),
                "sha256": sha256(args.existing_mapping.read_bytes()),
                "mapping_count": len(existing_indices),
            },
        },
        "code_domain": {
            "glyph_count": glyph_count,
            "bytes_per_glyph": GLYPH_SIZE,
            "first_code": f"{generated_codes[0]:04X}",
            "last_code": f"{generated_codes[-1]:04X}",
            "all_codes_unique": True,
            "all_code_index_roundtrips_pass": True,
            "forbidden_trail_7f_count": 0,
        },
        "demand": {
            "unique_hangul_count": len(hangul),
            "unique_hangul_first_occurrence_order": "".join(hangul),
            "unique_hangul_utf8_sha256": sha256("".join(hangul).encode("utf-8")),
            "translation_nonhangul_double_byte_code_count": len(translation_nonhangul_indices),
            "translation_nonhangul_double_byte_codes": [
                f"{index_to_sjis(i):04X}" for i in sorted(translation_nonhangul_indices)
            ],
        },
        "source_population": {
            "csv_rows_with_raw_source": raw_rows,
            "parsed_game_font_code_count": len(source_parsed),
            "all_adjacent_window_code_count": len(source_windows),
            "policy": (
                "Tier A reserves every game-font code found in any adjacent two-byte "
                "window of every exported CSV source row"
            ),
        },
        "original_font": {
            "all_zero_glyph_count": len(zero_indices),
            "uniform_nonzero_glyph_count": len(uniform_nonzero_indices),
            "nonzero_glyph_count": glyph_count - len(zero_indices),
            "all_zero_ranges": compact_ranges(sorted(zero_indices)),
        },
        "candidate_tiers": {
            "tier_a": {
                "count": len(tier_a),
                "definition": (
                    "code absent from every adjacent window of all exported CSV source "
                    "rows and not needed by final translation non-Hangul text"
                ),
                "all_zero_original_glyph_count": len(tier_a_zero),
                "nonzero_original_glyph_count": len(tier_a_nonzero),
                "ranges": compact_ranges(tier_a),
            },
            "tier_b_not_selected": {
                "count": len(tier_b),
                "definition": (
                    "absent at parsed character boundaries, but present in an unaligned "
                    "source-row window"
                ),
                "ranges": compact_ranges(tier_b),
            },
        },
        "whole_rom_window_signal": {
            "policy": (
                "adjacent raw pair counts across the complete binary are informational only; "
                "binary data is not treated as a text stream"
            ),
            "domain_codes_with_zero_hits": sum(count == 0 for count in whole_rom_counts),
            "tier_a_codes_with_zero_hits": sum(whole_rom_counts[i] == 0 for i in tier_a),
            "tier_a_min_hits": min((whole_rom_counts[i] for i in tier_a), default=0),
            "tier_a_max_hits": max((whole_rom_counts[i] for i in tier_a), default=0),
        },
        "selection": {
            "required_count": len(hangul),
            "selected_count": len(selected),
            "capacity_margin": len(tier_a) - len(hangul),
            "existing_runtime_proven_mapping_preserved": not existing_outside_tier_a,
            "selected_codes": selected_rows,
        },
        "gates": {
            "static_code_capacity": "pass" if len(selected) == len(hangul) else "fail",
            "known_text_collision": "pass" if len(selected) == len(hangul) else "not_run",
            "new_code_range_decoder_acceptance": "pending_runtime_poc",
            "other_font_consumers_and_nontext_references": "pending",
            "whole_game_runtime": "pending",
        },
        "not_proved": [
            "that every text or nontext consumer is represented in the exported CSV population",
            "that overwritten nonzero original glyphs have no consumer outside the known text population",
            "that newly selected code ranges are accepted by every runtime decoder path",
            "that incidental raw pair occurrences elsewhere in the ROM are unreachable",
            "whole-game rendering, layout, progression, and save-state behavior",
        ],
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--translation-csv", type=Path, required=True)
    parser.add_argument("--existing-mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
