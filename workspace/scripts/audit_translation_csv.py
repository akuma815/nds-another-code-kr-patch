#!/usr/bin/env python3
"""Audit a translator-edited CSV without modifying it.

The exported base CSV is the source/protection baseline.  Only the four fields
documented in README_translation_csv.md may differ in the translated CSV.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from functools import lru_cache
from pathlib import Path

from extract_script_candidates import DIRECTIVE
from export_translation_csv import FIELDS


EDITABLE_FIELDS = {
    "translation_text",
    "translation_status",
    "speaker_names",
    "translator_notes",
}
PROTECTED_FIELDS = [field for field in FIELDS if field not in EDITABLE_FIELDS]
ENCODING_ALIAS_CANDIDATES = {"·": "・"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {path}")
        return list(reader.fieldnames), list(reader)


def directive_sequence(text: str) -> list[str]:
    return [match.group(0) for match in DIRECTIVE.finditer(text)]


def functional_directive_sequence(text: str) -> list[str]:
    """Return directives whose position/count must match the source.

    ``%r`` is the renderer's in-window line break and is translator-owned
    layout.  Other directives drive events, windows, speakers, timing, or
    palette state and therefore remain protected by exact sequence matching.
    """
    return [token for token in directive_sequence(text) if token.lower() != "%r"]


def is_window_directive(token: str) -> bool:
    return len(token) > 3 and token[0] == "%" and token[1].lower() == "w" and token[2] == "("


def functional_directives_match(source: str, translation: str) -> tuple[bool, bool]:
    """Accept exact controls or added ``%h%w(same)`` page splits only."""
    expected = tuple(functional_directive_sequence(source))
    actual = tuple(functional_directive_sequence(translation))
    if expected == actual:
        return True, False

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

    return match(0, 0, ""), True


def literal_text(text: str) -> str:
    return DIRECTIVE.sub("", text)


def malformed_percent_offsets(text: str) -> list[int]:
    covered: set[int] = set()
    for match in DIRECTIVE.finditer(text):
        covered.update(range(match.start(), match.end()))
    return [index for index, char in enumerate(text) if char == "%" and index not in covered]


def is_hangul_syllable(char: str) -> bool:
    return "\uac00" <= char <= "\ud7a3"


def is_korean_non_syllable(char: str) -> bool:
    code = ord(char)
    return (
        0x1100 <= code <= 0x11FF
        or 0x3130 <= code <= 0x318F
        or 0xA960 <= code <= 0xA97F
        or 0xD7B0 <= code <= 0xD7FF
    )


def is_japanese_or_cjk(char: str) -> bool:
    code = ord(char)
    return (
        0x3040 <= code <= 0x30FF
        or 0x31F0 <= code <= 0x31FF
        or 0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0xFF66 <= code <= 0xFF9F
    )


def encoded_length_and_unsupported(text: str) -> tuple[int | None, list[str]]:
    """Estimate production bytes: Hangul syllables are two bytes, others CP932."""
    length = 0
    unsupported: list[str] = []
    for char in text:
        if is_hangul_syllable(char):
            length += 2
            continue
        try:
            length += len(char.encode("cp932"))
        except UnicodeEncodeError:
            unsupported.append(char)
    if unsupported:
        return None, sorted(set(unsupported), key=ord)
    return length, []


def row_ref(row: dict[str, str]) -> dict[str, str]:
    return {
        "text_id": row["text_id"],
        "population": row["population"],
        "scene_id": row["scene_id"],
        "component_offset_hex": row["component_offset_hex"],
    }


def audit(base_path: Path, translated_path: Path) -> dict:
    base_header, base_rows = read_csv(base_path)
    translated_header, translated_rows = read_csv(translated_path)

    base_ids = [row.get("text_id", "") for row in base_rows]
    translated_ids = [row.get("text_id", "") for row in translated_rows]
    base_by_id = {row["text_id"]: row for row in base_rows}
    translated_by_id = {row["text_id"]: row for row in translated_rows}

    duplicate_base_ids = sorted(text_id for text_id, count in Counter(base_ids).items() if count > 1)
    duplicate_translated_ids = sorted(
        text_id for text_id, count in Counter(translated_ids).items() if count > 1
    )
    missing_ids = [text_id for text_id in base_ids if text_id not in translated_by_id]
    extra_ids = [text_id for text_id in translated_ids if text_id not in base_by_id]

    protected_mismatches: list[dict] = []
    editable_change_counts = Counter()
    comparable_ids = [
        text_id
        for text_id in base_ids
        if text_id in translated_by_id and text_id in base_by_id
    ]
    for text_id in comparable_ids:
        base_row = base_by_id[text_id]
        translated_row = translated_by_id[text_id]
        for field in PROTECTED_FIELDS:
            if base_row.get(field, "") != translated_row.get(field, ""):
                protected_mismatches.append(
                    {
                        "text_id": text_id,
                        "field": field,
                        "base_value": base_row.get(field, ""),
                        "translated_value": translated_row.get(field, ""),
                    }
                )
        for field in EDITABLE_FIELDS:
            if base_row.get(field, "") != translated_row.get(field, ""):
                editable_change_counts[field] += 1

    population_total = Counter()
    population_filled = Counter()
    translation_status_counts = Counter()
    filled_rows: list[dict[str, str]] = []
    blank_rows: list[dict[str, str]] = []
    source_equal_rows: list[dict[str, str]] = []
    directive_mismatches: list[dict] = []
    safe_page_splits: list[dict] = []
    malformed_percent_rows: list[dict] = []
    unsupported_rows: list[dict] = []
    japanese_residue_rows: list[dict] = []
    korean_non_syllable_rows: list[dict] = []
    literal_newline_rows: list[dict] = []
    length_rows: list[dict] = []
    unique_hangul: set[str] = set()
    all_literal_character_counts = Counter()

    for row in translated_rows:
        population = row.get("population", "")
        population_total[population] += 1
        translation_status_counts[row.get("translation_status", "")] += 1
        translation = row.get("translation_text", "")
        if not translation.strip():
            blank_rows.append(row_ref(row))
            continue

        filled_rows.append(row_ref(row))
        population_filled[population] += 1
        source = row.get("source_text", "")
        if translation == source:
            source_equal_rows.append(row_ref(row))

        source_directives = directive_sequence(source)
        translated_directives = directive_sequence(translation)
        source_functional = functional_directive_sequence(source)
        translated_functional = functional_directive_sequence(translation)
        directives_match, used_safe_page_split = functional_directives_match(source, translation)
        if not directives_match:
            directive_mismatches.append(
                {
                    **row_ref(row),
                    "source_directives": source_directives,
                    "translation_directives": translated_directives,
                    "source_functional_directives": source_functional,
                    "translation_functional_directives": translated_functional,
                }
            )
        elif used_safe_page_split:
            safe_page_splits.append(
                {
                    **row_ref(row),
                    "source_functional_directives": source_functional,
                    "translation_functional_directives": translated_functional,
                }
            )

        malformed_offsets = malformed_percent_offsets(translation)
        if malformed_offsets:
            malformed_percent_rows.append(
                {**row_ref(row), "offsets": malformed_offsets}
            )

        literal = literal_text(translation)
        all_literal_character_counts.update(literal)
        unique_hangul.update(char for char in literal if is_hangul_syllable(char))
        japanese = sorted({char for char in literal if is_japanese_or_cjk(char)}, key=ord)
        if japanese:
            japanese_residue_rows.append(
                {**row_ref(row), "characters": japanese}
            )
        non_syllables = sorted({char for char in literal if is_korean_non_syllable(char)}, key=ord)
        if non_syllables:
            korean_non_syllable_rows.append(
                {**row_ref(row), "characters": non_syllables}
            )
        if "\n" in translation or "\r" in translation:
            literal_newline_rows.append(row_ref(row))

        aliased_translation = "".join(
            ENCODING_ALIAS_CANDIDATES.get(char, char) for char in translation
        )
        encoded_length, unsupported = encoded_length_and_unsupported(aliased_translation)
        if unsupported:
            unsupported_rows.append(
                {**row_ref(row), "characters": unsupported}
            )
            continue
        source_length = int(row["source_byte_length"])
        delta = encoded_length - source_length
        length_rows.append(
            {
                **row_ref(row),
                "source_byte_length": source_length,
                "estimated_translation_byte_length": encoded_length,
                "delta": delta,
                "fixed_slot_fit": delta <= 0,
            }
        )

    fixed_fit = [row for row in length_rows if row["fixed_slot_fit"]]
    fixed_overflow = [row for row in length_rows if not row["fixed_slot_fit"]]
    fixed_overflow.sort(key=lambda row: (-row["delta"], row["text_id"]))
    length_deltas = [row["delta"] for row in length_rows]

    structure_pass = (
        base_header == FIELDS
        and translated_header == FIELDS
        and len(base_rows) == len(translated_rows)
        and base_ids == translated_ids
        and not duplicate_base_ids
        and not duplicate_translated_ids
        and not missing_ids
        and not extra_ids
        and not protected_mismatches
    )
    deterministic_translation_pass = (
        not directive_mismatches
        and not malformed_percent_rows
        and not unsupported_rows
        and not korean_non_syllable_rows
        and not literal_newline_rows
    )

    population_summary = {}
    for population in sorted(population_total):
        total = population_total[population]
        filled = population_filled[population]
        population_summary[population] = {
            "total": total,
            "filled": filled,
            "blank": total - filled,
            "filled_percent": round(filled * 100 / total, 2) if total else 0.0,
        }

    return {
        "schema_version": 1,
        "status": "translation_csv_audit_complete",
        "baseline": {
            "path": str(base_path),
            "size": base_path.stat().st_size,
            "sha256": sha256(base_path),
            "utf8_bom": base_path.read_bytes().startswith(b"\xef\xbb\xbf"),
        },
        "translated_ssot": {
            "path": str(translated_path),
            "size": translated_path.stat().st_size,
            "sha256": sha256(translated_path),
            "utf8_bom": translated_path.read_bytes().startswith(b"\xef\xbb\xbf"),
            "read_only_audit": True,
        },
        "schema": {
            "expected_column_count": len(FIELDS),
            "baseline_header_exact": base_header == FIELDS,
            "translated_header_exact": translated_header == FIELDS,
            "baseline_row_count": len(base_rows),
            "translated_row_count": len(translated_rows),
            "row_order_and_ids_exact": base_ids == translated_ids,
            "duplicate_baseline_ids": duplicate_base_ids,
            "duplicate_translated_ids": duplicate_translated_ids,
            "missing_ids": missing_ids,
            "extra_ids": extra_ids,
        },
        "protection": {
            "editable_fields": sorted(EDITABLE_FIELDS),
            "protected_field_count": len(PROTECTED_FIELDS),
            "protected_mismatch_count": len(protected_mismatches),
            "protected_mismatches": protected_mismatches,
            "editable_change_counts_from_baseline": dict(sorted(editable_change_counts.items())),
            "pass": not protected_mismatches,
        },
        "translation_coverage": {
            "total_rows": len(translated_rows),
            "filled_rows": len(filled_rows),
            "blank_rows": len(blank_rows),
            "filled_percent": round(len(filled_rows) * 100 / len(translated_rows), 2),
            "source_equal_rows": len(source_equal_rows),
            "by_population": population_summary,
            "translation_status_counts": dict(sorted(translation_status_counts.items())),
        },
        "deterministic_checks": {
            "directive_mismatch_count": len(directive_mismatches),
            "safe_repeated_window_page_split_count": len(safe_page_splits),
            "layout_linebreak_difference_count": sum(
                directive_sequence(row.get("source_text", "")).count("%r")
                != directive_sequence(row.get("translation_text", "")).count("%r")
                for row in translated_rows
                if row.get("translation_text", "").strip()
            ),
            "malformed_percent_row_count": len(malformed_percent_rows),
            "unsupported_unicode_row_count": len(unsupported_rows),
            "korean_non_syllable_row_count": len(korean_non_syllable_rows),
            "literal_newline_row_count": len(literal_newline_rows),
            "pass": deterministic_translation_pass,
        },
        "review_signals": {
            "japanese_or_cjk_residue_row_count": len(japanese_residue_rows),
            "source_equal_row_count": len(source_equal_rows),
            "note": "Directive arguments are excluded from residue detection.",
        },
        "glyph_demand": {
            "unique_hangul_syllable_count": len(unique_hangul),
            "unique_hangul_syllables": "".join(sorted(unique_hangul)),
            "literal_unique_character_count": len(all_literal_character_counts),
            "hangul_mapping_bytes_per_syllable": 2,
            "current_poc_mapping_capacity": 61,
            "current_poc_capacity_sufficient": len(unique_hangul) <= 61,
            "note": "61 is the conservative PoC pool, not the final font_lc.bin capacity.",
        },
        "fixed_slot_estimate": {
            "eligible_for_length_estimate": len(length_rows),
            "fit_count": len(fixed_fit),
            "overflow_count": len(fixed_overflow),
            "fit_percent": round(len(fixed_fit) * 100 / len(length_rows), 2) if length_rows else 0.0,
            "max_overflow_bytes": max(length_deltas, default=0),
            "total_delta_bytes": sum(length_deltas),
            "note": "Estimate uses 2 bytes per Hangul syllable and CP932 for all other characters.",
        },
        "gates": {
            "csv_structure_and_protection": "pass" if structure_pass else "fail",
            "deterministic_translation_integrity": "pass" if deterministic_translation_pass else "fail",
            "fixed_length_reinsertion": "pass" if not fixed_overflow and deterministic_translation_pass else "blocked",
            "production_build": "blocked_pending_font_mapping_reinsertion_and_runtime_qa",
        },
        "queues": {
            "blank_rows": blank_rows,
            "source_equal_rows": source_equal_rows,
            "directive_mismatches": directive_mismatches,
            "safe_repeated_window_page_splits": safe_page_splits,
            "malformed_percent_rows": malformed_percent_rows,
            "unsupported_unicode_rows": unsupported_rows,
            "korean_non_syllable_rows": korean_non_syllable_rows,
            "literal_newline_rows": literal_newline_rows,
            "japanese_or_cjk_residue_rows": japanese_residue_rows,
            "fixed_slot_overflow_rows": fixed_overflow,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--translated", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.baseline, args.translated)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: report[key] for key in ("status", "translation_coverage", "deterministic_checks", "glyph_demand", "fixed_slot_estimate", "gates")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
