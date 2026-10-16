#!/usr/bin/env python3
"""Generate a deterministic translation-derived candidate mapping from audited inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


EXPECTED_RUNTIME_STATUS = "runtime_pass_user_confirmed_representative_leads"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def allocate_full_mapping(
    characters: list[str], selected_codes: list[dict], existing: dict
) -> list[dict]:
    if len(characters) != len(set(characters)):
        raise ValueError("Hangul demand contains duplicate characters")
    candidates = [
        (int(row["code"], 16), int(row["glyph_index"]), row)
        for row in selected_codes
    ]
    pairs = [(code, index) for code, index, _row in candidates]
    if len(pairs) != len(set(pairs)):
        raise ValueError("selected candidate code/index pairs are not unique")
    by_pair = {(code, index): row for code, index, row in candidates}

    preserved: dict[str, tuple[int, int]] = {}
    for row in existing["mappings"]:
        char = row["character"]
        pair = (int(row["code"], 16), int(row["glyph_index"]))
        if char not in characters:
            raise ValueError(f"existing mapping character is outside final demand: {char}")
        if pair not in by_pair:
            raise ValueError(f"existing mapping pair is outside audited selection: {row}")
        if char in preserved:
            raise ValueError(f"duplicate existing mapping character: {char}")
        preserved[char] = pair

    reserved = set(preserved.values())
    available = iter(pair for pair in pairs if pair not in reserved)
    result: list[dict] = []
    for char in characters:
        pair = preserved.get(char)
        if pair is None:
            pair = next(available, None)
        if pair is None:
            raise ValueError("audited selection is smaller than the final Hangul demand")
        code, index = pair
        candidate = by_pair[pair]
        result.append(
            {
                "character": char,
                "code": f"{code:04X}",
                "glyph_index": index,
                "original_glyph": candidate["original_glyph"],
                "cp932_status": candidate["cp932_status"],
                "mapping_origin": (
                    "preserved_runtime_intro_mapping"
                    if char in preserved
                    else "full_pool_static_selection"
                ),
            }
        )
    if len({row["code"] for row in result}) != len(result):
        raise AssertionError("generated mapping codes are not unique")
    return result


def generate(args: argparse.Namespace) -> dict:
    audit_bytes = args.audit.read_bytes()
    audit = json.loads(audit_bytes.decode("utf-8"))
    existing = json.loads(args.existing_mapping.read_text(encoding="utf-8"))
    font = json.loads(args.font_config.read_text(encoding="utf-8"))
    runtime = json.loads(args.runtime_result.read_text(encoding="utf-8"))

    if audit["status"] != "static_capacity_pass_runtime_range_gate_pending":
        raise ValueError("full code-pool audit has not passed static capacity")
    if runtime["status"] != EXPECTED_RUNTIME_STATUS:
        raise ValueError("representative lead-byte runtime gate has not passed")
    if runtime["outcome"] != "pass_for_representative_lead_byte_decoder_and_glyph_display":
        raise ValueError("representative lead-byte runtime outcome mismatch")
    if font.get("selection_status") != "approved_by_user_runtime_qa":
        raise ValueError("font selection is not runtime-approved")

    baseline = audit["baseline"]
    source_sha = baseline["rom"]["sha256"]
    font_sha = font["source"]["sha256"]
    if existing["source_sha256"] != source_sha:
        raise ValueError("existing mapping/source identity mismatch")
    if existing["font_sha256"] != font_sha:
        raise ValueError("existing mapping/font identity mismatch")
    if baseline["font"]["sha256"] != "237924FBFE3B77A0F9E63FC12B352AF48639EB7E479581416734DBFC84B47613":
        raise ValueError("unexpected original game font identity")

    characters = list(audit["demand"]["unique_hangul_first_occurrence_order"])
    selected = audit["selection"]["selected_codes"]
    if len(characters) != audit["demand"]["unique_hangul_count"]:
        raise ValueError("audit Hangul demand count mismatch")
    if len(selected) != audit["selection"]["selected_count"]:
        raise ValueError("audit selected-code count mismatch")
    if len(characters) != len(selected):
        raise ValueError("audited selection does not exactly cover final Hangul demand")

    selected_leads = sorted({row["code"][:2] for row in selected})
    runtime_leads = sorted(runtime["target"]["lead_byte_coverage"])
    if selected_leads != runtime_leads:
        raise ValueError(
            f"runtime lead coverage mismatch: selected={selected_leads}, runtime={runtime_leads}"
        )

    mappings = allocate_full_mapping(characters, selected, existing)
    preserved_count = sum(
        row["mapping_origin"] == "preserved_runtime_intro_mapping" for row in mappings
    )
    if preserved_count != len(existing["mappings"]):
        raise AssertionError("not all existing runtime mappings were preserved")

    return {
        "schema_version": 1,
        "mapping_id": "another-code-full-hangul-candidate-v1",
        "status": "static_candidate_mapping_runtime_leads_pass_nontext_gate_pending",
        "source_sha256": source_sha,
        "translation_ssot_sha256": baseline["translation_ssot"]["sha256"],
        "original_font_sha256": baseline["font"]["sha256"],
        "font_source_sha256": font_sha,
        "audit": {
            "path": str(args.audit),
            "sha256": sha256(audit_bytes),
            "tier": "tier_a",
        },
        "runtime_gate": {
            "path": str(args.runtime_result),
            "sha256": sha256(args.runtime_result.read_bytes()),
            "status": runtime["status"],
            "lead_byte_coverage": runtime_leads,
            "scope": "representative codes in the main intro renderer",
        },
        "character_count": len(mappings),
        "preserved_existing_mapping_count": preserved_count,
        "all_zero_original_glyph_count": sum(
            row["original_glyph"] == "all_zero" for row in mappings
        ),
        "nonzero_original_glyph_count": sum(
            row["original_glyph"] == "nonzero" for row in mappings
        ),
        "allocation_order": (
            "final CSV Hangul first-occurrence order; preserve existing intro mappings; "
            "then consume the audited selected-code order"
        ),
        "mappings": mappings,
        "gates": {
            "static_capacity": "pass",
            "known_csv_text_collision": "pass",
            "representative_lead_runtime": "pass",
            "all_generated_code_pairs_runtime": "not_individually_tested",
            "csv_external_and_nontext_consumers": "pending",
            "production_build_eligibility": "pending",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--existing-mapping", type=Path, required=True)
    parser.add_argument("--font-config", type=Path, required=True)
    parser.add_argument("--runtime-result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = generate(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
