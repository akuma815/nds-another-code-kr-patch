#!/usr/bin/env python3
"""Export the current reversible text catalog as a translator-facing UTF-8 CSV."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from collections import Counter
from pathlib import Path

from extract_script_candidates import DIRECTIVE


OVERLAY = re.compile(r"arm9_overlay_(\d{3})_file_(\d{3})$")


FIELDS = [
    "row_order",
    "text_id",
    "population",
    "catalog_status",
    "translation_status",
    "scene_id",
    "scene_order",
    "scene_status",
    "asset_scene_ids",
    "component",
    "overlay_id",
    "component_offset_hex",
    "event_ids",
    "speaker_codes",
    "speaker_sequence",
    "speaker_names",
    "speaker_status",
    "image_ids",
    "actor_ids",
    "source_text",
    "source_preview",
    "translation_text",
    "source_byte_length",
    "source_roundtrip_equal",
    "literal_character_count",
    "directive_sequence",
    "directive_policy",
    "reference_count",
    "reference_offsets_hex",
    "table_id",
    "confidence",
    "reinsertion_status",
    "source_raw_hex",
    "source_rom_sha256",
    "translator_notes",
]


def ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def scene_fields(component: str) -> tuple[str, str, str, str]:
    match = OVERLAY.fullmatch(component)
    if match:
        overlay_id = match.group(1)
        return (
            f"overlay_{overlay_id}",
            str(int(overlay_id)),
            "structural_overlay_group_not_runtime_order",
            overlay_id,
        )
    if component == "arm9":
        return "global_arm9", "", "global_component_context_unknown", ""
    if component == "arm7":
        return "global_arm7", "", "global_component_context_unknown", ""
    return component, "", "component_group_context_unknown", ""


def directive_values(text: str, token: str) -> list[str]:
    return [
        match.group(2) or ""
        for match in DIRECTIVE.finditer(text)
        if match.group(1) == token
    ]


def literal_text(text: str) -> str:
    return DIRECTIVE.sub("", text)


def preview_text(text: str) -> str:
    result = []
    cursor = 0
    for match in DIRECTIVE.finditer(text):
        result.append(text[cursor : match.start()])
        if match.group(1) in {"r", "h"}:
            result.append(" ⏎ ")
        cursor = match.end()
    result.append(text[cursor:])
    return "".join(result).strip()


def speaker_fields(text: str) -> tuple[str, str, str]:
    sequence = directive_values(text, "C")
    unique = ordered_unique(sequence)
    if not unique:
        status = "no_confirmed_speaker_code"
    elif len(unique) == 1:
        status = "single_unresolved_speaker_code"
    else:
        status = "multiple_unresolved_speaker_codes_in_record"
    return ";".join(unique), ">".join(sequence), status


def load_seeds(paths: list[Path]) -> dict[str, dict]:
    seeds = {}
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("entries", [data])
        for entry in entries:
            text_id = entry["text_id"]
            if text_id in seeds and seeds[text_id]["translation_text"] != entry["translation_text"]:
                raise ValueError(f"conflicting seed translations for {text_id}")
            seeds[text_id] = entry
    return seeds


def make_row(
    index: int,
    record: dict,
    population: str,
    reference: dict | None,
    seed: dict | None,
    source_sha256: str,
) -> dict:
    text = record["cp932"]
    scene_id, scene_order, scene_status, overlay_id = scene_fields(record["component"])
    speaker_codes, speaker_sequence, speaker_status = speaker_fields(text)
    directives = [match.group(0) for match in DIRECTIVE.finditer(text)]
    image_ids = ordered_unique(directive_values(text, "i"))
    actor_ids = ordered_unique(directive_values(text, "n"))
    asset_scene_ids = ordered_unique(
        value[:3] for value in image_ids + actor_ids if re.fullmatch(r"P\d{4}", value)
    )
    if population == "strong_script":
        catalog_status = "accepted_reversible_catalog"
        reinsertion_status = "fixed_length_build_path_proved_for_subset"
    elif population == "auxiliary":
        catalog_status = "accepted_candidate_catalog_not_runtime_confirmed"
        reinsertion_status = "needs_consumer_and_layout_qa"
    else:
        catalog_status = "review_required_not_in_accepted_catalog"
        reinsertion_status = "not_build_eligible"
    translation_text = seed["translation_text"] if seed else ""
    translation_status = seed.get("status", "seeded_poc") if seed else "not_started"
    offsets = reference["reference_offsets"] if reference else []
    return {
        "row_order": index,
        "text_id": record["text_id"],
        "population": population,
        "catalog_status": catalog_status,
        "translation_status": translation_status,
        "scene_id": scene_id,
        "scene_order": scene_order,
        "scene_status": scene_status,
        "asset_scene_ids": ";".join(asset_scene_ids),
        "component": record["component"],
        "overlay_id": overlay_id,
        "component_offset_hex": f"0x{record['offset']:06X}",
        "event_ids": ";".join(ordered_unique(directive_values(text, "v"))),
        "speaker_codes": speaker_codes,
        "speaker_sequence": speaker_sequence,
        "speaker_names": "",
        "speaker_status": speaker_status,
        "image_ids": ";".join(image_ids),
        "actor_ids": ";".join(actor_ids),
        "source_text": text,
        "source_preview": preview_text(text),
        "translation_text": translation_text,
        "source_byte_length": record["length"],
        "source_roundtrip_equal": "yes" if record["roundtrip_equal"] else "no",
        "literal_character_count": len(literal_text(text)),
        "directive_sequence": " ".join(directives),
        "directive_policy": "preserve_exact_sequence_and_arguments",
        "reference_count": reference["reference_count"] if reference else 0,
        "reference_offsets_hex": ";".join(f"0x{offset:06X}" for offset in offsets),
        "table_id": record.get("table_id", ""),
        "confidence": record["confidence"],
        "reinsertion_status": reinsertion_status,
        "source_raw_hex": record["raw_hex"],
        "source_rom_sha256": source_sha256,
        "translator_notes": seed.get("notes", "") if seed else "",
    }


def export(args: argparse.Namespace) -> dict:
    scripts = json.loads(args.script_report.read_text(encoding="utf-8"))["records"]
    auxiliary = json.loads(args.aux_report.read_text(encoding="utf-8"))["records"]
    population_report = json.loads(args.population_report.read_text(encoding="utf-8"))
    isolated = population_report.get("unresolved_isolated_records", [])
    reference_report = json.loads(args.reference_report.read_text(encoding="utf-8"))
    references = {row["text_id"]: row for row in reference_report["catalog"]}
    source_sha256 = reference_report["source_sha256"]
    seeds = load_seeds(args.seed_translation)

    records = [
        *(('strong_script', row) for row in scripts),
        *(('auxiliary', row) for row in auxiliary),
        *(('isolated_candidate', row) for row in isolated),
    ]
    records.sort(key=lambda item: (item[1]["component"], item[1]["offset"], item[0]))
    rows = [
        make_row(index, row, kind, references.get(row["text_id"]), seeds.get(row["text_id"]), source_sha256)
        for index, (kind, row) in enumerate(records, 1)
    ]
    if len({row["text_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate text_id in translation export")
    unknown_seeds = sorted(set(seeds) - {row["text_id"] for row in rows})
    if unknown_seeds:
        raise ValueError(f"seed translations not found in catalog: {unknown_seeds}")

    if args.output.exists() and not args.force:
        raise FileExistsError(
            f"refusing to overwrite translation CSV without --force: {args.output}"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    with args.output.open("r", encoding="utf-8-sig", newline="") as handle:
        parsed = list(csv.DictReader(handle))
    if len(parsed) != len(rows) or parsed[0]["text_id"] != rows[0]["text_id"]:
        raise ValueError("CSV readback verification failed")
    for original, parsed_row in zip(rows, parsed):
        if original["source_roundtrip_equal"] == "no":
            continue
        if parsed_row["source_text"].encode("cp932").hex().upper() != original["source_raw_hex"]:
            raise ValueError(f"source roundtrip failed: {original['text_id']}")

    population_counts = Counter(row["population"] for row in rows)
    report = {
        "schema_version": 1,
        "status": "csv_export_pass",
        "source_rom_sha256": source_sha256,
        "output": str(args.output),
        "output_size": args.output.stat().st_size,
        "output_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest().upper(),
        "encoding": "UTF-8 with BOM",
        "column_count": len(FIELDS),
        "row_count": len(rows),
        "unique_text_id_count": len({row["text_id"] for row in rows}),
        "population_counts": dict(sorted(population_counts.items())),
        "seeded_translation_count": sum(bool(row["translation_text"]) for row in rows),
        "source_cp932_roundtrip_count": sum(row["source_roundtrip_equal"] == "yes" for row in rows),
        "source_cp932_nonroundtrip_review_count": sum(row["source_roundtrip_equal"] == "no" for row in rows),
        "reference_covered_count": sum(int(row["reference_count"]) > 0 for row in rows),
        "speaker_code_record_count": sum(bool(row["speaker_codes"]) for row in rows),
        "scene_overlay_record_count": sum(bool(row["overlay_id"]) for row in rows),
        "limitations": [
            "scene_id is a structural overlay group and is not guaranteed runtime order",
            "speaker codes are preserved but character-name meanings are not yet proved",
            "isolated candidates are included for review but are not build eligible",
            "opaque NitroFS pack resources and baked graphic text remain outside this CSV",
        ],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script-report", type=Path, required=True)
    parser.add_argument("--aux-report", type=Path, required=True)
    parser.add_argument("--population-report", type=Path, required=True)
    parser.add_argument("--reference-report", type=Path, required=True)
    parser.add_argument("--seed-translation", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    report = export(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
