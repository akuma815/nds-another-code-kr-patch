#!/usr/bin/env python3
"""Prove whether translated records can be permuted among existing text slots.

This is a static feasibility analysis only.  It does not write a ROM and does
not claim that padding or every auxiliary consumer has passed runtime QA.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from audit_translation_csv import ENCODING_ALIAS_CANDIDATES, encoded_length_and_unsupported


ACCEPTED_POPULATIONS = {"strong_script", "auxiliary"}
OVERLAY_COMPONENT = re.compile(r"arm9_overlay_(\d{3})_file_(\d{3})")
def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def apply_alias_candidates(text: str) -> str:
    return "".join(ENCODING_ALIAS_CANDIDATES.get(char, char) for char in text)


def component_path(extracted_root: Path, component: str) -> Path:
    if component == "arm9":
        return extracted_root / "arm9" / "arm9.bin"
    if component == "arm7":
        return extracted_root / "arm7" / "arm7.bin"
    if OVERLAY_COMPONENT.fullmatch(component):
        return extracted_root / "overlays" / f"{component}.bin"
    raise ValueError(f"unsupported component: {component}")


def load_address(inventory: dict, component: str) -> int:
    if component == "arm9":
        return int(inventory["header"]["arm9"]["load_address"])
    if component == "arm7":
        return int(inventory["header"]["arm7"]["load_address"])
    match = OVERLAY_COMPONENT.fullmatch(component)
    if not match:
        raise ValueError(f"unsupported component: {component}")
    overlay_id = int(match.group(1))
    file_id = int(match.group(2))
    row = next(
        (
            item
            for item in inventory["arm9_overlays"]
            if int(item["overlay_id"]) == overlay_id and int(item["file_id"]) == file_id
        ),
        None,
    )
    if row is None:
        raise ValueError(f"overlay missing from inventory: {component}")
    if int(row["compressed_size"]) != 0 or int(row["flags"]) != 0:
        raise ValueError(f"compressed or flagged overlay is outside this analysis: {component}")
    return int(row["ram_address"])


def sorted_capacity_assignment(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return deterministic target-to-slot assignment and any capacity failures."""
    slots = sorted(rows, key=lambda row: (row["source_capacity"], row["text_id"]))
    targets = sorted(rows, key=lambda row: (row["translation_length"], row["text_id"]))
    assignments = []
    failures = []
    for target, slot in zip(targets, slots):
        result = {
            "translation_text_id": target["text_id"],
            "translation_length": target["translation_length"],
            "slot_text_id": slot["text_id"],
            "slot_offset": slot["offset"],
            "slot_capacity": slot["source_capacity"],
            "remaining_bytes": slot["source_capacity"] - target["translation_length"],
            "stays_in_original_slot": target["text_id"] == slot["text_id"],
        }
        assignments.append(result)
        if result["remaining_bytes"] < 0:
            failures.append(result)
    return assignments, failures


def pack_multiple_records(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Best-fit-decreasing packing into the exact original slot+NUL ranges."""
    bins = [
        {
            "slot_text_id": row["text_id"],
            "slot_offset": row["offset"],
            "capacity": row["source_capacity"] + 1,
            "used": 0,
        }
        for row in rows
    ]
    placements = []
    failures = []
    for target in sorted(
        rows, key=lambda row: (-(row["translation_length"] + 1), row["text_id"])
    ):
        required = target["translation_length"] + 1
        candidates = [slot for slot in bins if slot["capacity"] - slot["used"] >= required]
        if not candidates:
            failures.append(
                {
                    "translation_text_id": target["text_id"],
                    "required_bytes_including_nul": required,
                    "largest_remaining_region": max(
                        (slot["capacity"] - slot["used"] for slot in bins), default=0
                    ),
                }
            )
            continue
        slot = min(
            candidates,
            key=lambda item: (item["capacity"] - item["used"] - required, item["slot_text_id"]),
        )
        placements.append(
            {
                "translation_text_id": target["text_id"],
                "translation_length": target["translation_length"],
                "slot_text_id": slot["slot_text_id"],
                "target_offset": slot["slot_offset"] + slot["used"],
                "bytes_including_nul": required,
            }
        )
        slot["used"] += required
    return placements, failures


def merge_zero_aligned_slots(
    data: bytes,
    intervals: list[tuple[int, int, str]],
    pointer_ranges: list[tuple[int, int]],
    max_gap: int = 3,
) -> tuple[list[dict], int]:
    """Merge adjacent source slots across at most max_gap confirmed zero bytes."""
    clusters: list[dict] = []
    borrowed_zero_bytes = 0
    for start, end, text_id in sorted(intervals):
        if not clusters:
            clusters.append({"start": start, "end": end, "member_text_ids": [text_id]})
            continue
        cluster = clusters[-1]
        gap_start = cluster["end"]
        gap_end = start
        gap_size = gap_end - gap_start
        pointer_overlap = any(left < gap_end and right > gap_start for left, right in pointer_ranges)
        gap_is_zero_alignment = (
            0 <= gap_size <= max_gap
            and data[gap_start:gap_end] == b"\0" * gap_size
            and not pointer_overlap
        )
        if gap_is_zero_alignment:
            borrowed_zero_bytes += gap_size
            cluster["end"] = end
            cluster["member_text_ids"].append(text_id)
        else:
            clusters.append({"start": start, "end": end, "member_text_ids": [text_id]})
    for index, cluster in enumerate(clusters, 1):
        cluster["cluster_id"] = index
        cluster["capacity"] = cluster["end"] - cluster["start"]
    return clusters, borrowed_zero_bytes


def pack_records_into_regions(
    rows: list[dict], regions: list[dict]
) -> tuple[list[dict], list[dict]]:
    bins = [dict(region, used=0) for region in regions]
    placements = []
    failures = []
    for target in sorted(
        rows, key=lambda row: (-(row["translation_length"] + 1), row["text_id"])
    ):
        required = target["translation_length"] + 1
        candidates = [slot for slot in bins if slot["capacity"] - slot["used"] >= required]
        if not candidates:
            failures.append(
                {
                    "translation_text_id": target["text_id"],
                    "required_bytes_including_nul": required,
                    "largest_remaining_region": max(
                        (slot["capacity"] - slot["used"] for slot in bins), default=0
                    ),
                }
            )
            continue
        slot = min(
            candidates,
            key=lambda item: (item["capacity"] - item["used"] - required, item["start"]),
        )
        placements.append(
            {
                "translation_text_id": target["text_id"],
                "translation_length": target["translation_length"],
                "cluster_id": slot["cluster_id"],
                "target_offset": slot["start"] + slot["used"],
                "bytes_including_nul": required,
            }
        )
        slot["used"] += required
    return placements, failures


def analyze(
    translated_csv: Path,
    audit_report_path: Path,
    reference_report_path: Path,
    inventory_path: Path,
    extracted_root: Path,
) -> dict:
    audit_report = json.loads(audit_report_path.read_text(encoding="utf-8"))
    references = json.loads(reference_report_path.read_text(encoding="utf-8"))
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    rows = [
        row
        for row in read_rows(translated_csv)
        if row["population"] in ACCEPTED_POPULATIONS
    ]
    reference_by_id = {row["text_id"]: row for row in references["catalog"]}
    grouped: dict[str, list[dict]] = defaultdict(list)
    unresolved_encoding: list[dict] = []
    alias_usage: list[dict] = []

    for row in rows:
        if not row["translation_text"].strip():
            raise ValueError(f"accepted catalog translation is blank: {row['text_id']}")
        direct_length, unsupported = encoded_length_and_unsupported(row["translation_text"])
        aliased_text = apply_alias_candidates(row["translation_text"])
        aliased_length, still_unsupported = encoded_length_and_unsupported(aliased_text)
        if unsupported:
            alias_usage.append(
                {
                    "text_id": row["text_id"],
                    "characters": unsupported,
                    "candidate_substitutions": {
                        char: ENCODING_ALIAS_CANDIDATES[char]
                        for char in unsupported
                        if char in ENCODING_ALIAS_CANDIDATES
                    },
                }
            )
        if still_unsupported or aliased_length is None:
            unresolved_encoding.append(
                {"text_id": row["text_id"], "characters": still_unsupported}
            )
            continue
        grouped[row["component"]].append(
            {
                "text_id": row["text_id"],
                "population": row["population"],
                "offset": int(row["component_offset_hex"], 16),
                "source_capacity": int(row["source_byte_length"]),
                "translation_length": aliased_length,
                "source_raw": bytes.fromhex(row["source_raw_hex"]),
                "direct_encoding_length": direct_length,
            }
        )

    source_byte_mismatches: list[dict] = []
    terminator_mismatches: list[dict] = []
    overlapping_slots: list[dict] = []
    pointer_mismatches: list[dict] = []
    pointer_inside_slot: list[dict] = []
    missing_references: list[str] = []
    component_reports = []
    all_assignments = []

    for component in sorted(grouped):
        component_rows = grouped[component]
        path = component_path(extracted_root, component)
        data = path.read_bytes()
        base = load_address(inventory, component)
        intervals = []
        pointer_ranges = []
        for row in component_rows:
            start = row["offset"]
            end = start + row["source_capacity"]
            intervals.append((start, end + 1, row["text_id"]))
            if data[start:end] != row["source_raw"]:
                source_byte_mismatches.append(
                    {"text_id": row["text_id"], "component": component, "offset": start}
                )
            if end >= len(data) or data[end] != 0:
                terminator_mismatches.append(
                    {"text_id": row["text_id"], "component": component, "offset": end}
                )

            reference = reference_by_id.get(row["text_id"])
            if reference is None:
                missing_references.append(row["text_id"])
                continue
            expected_pointer = (base + start).to_bytes(4, "little")
            if reference["pointer_encoding"] != "u32le_absolute_ram":
                pointer_mismatches.append(
                    {"text_id": row["text_id"], "reason": "unsupported_pointer_encoding"}
                )
            if bytes.fromhex(reference["pointer_raw_hex"]) != expected_pointer:
                pointer_mismatches.append(
                    {"text_id": row["text_id"], "reason": "catalog_target_mismatch"}
                )
            for pointer_offset in reference["reference_offsets"]:
                pointer_ranges.append((pointer_offset, pointer_offset + 4))
                if data[pointer_offset : pointer_offset + 4] != expected_pointer:
                    pointer_mismatches.append(
                        {
                            "text_id": row["text_id"],
                            "reason": "stored_pointer_bytes_mismatch",
                            "pointer_offset": pointer_offset,
                        }
                    )

        intervals.sort()
        for left, right in zip(intervals, intervals[1:]):
            if right[0] < left[1]:
                overlapping_slots.append(
                    {"component": component, "left_text_id": left[2], "right_text_id": right[2]}
                )
        for row in component_rows:
            reference = reference_by_id.get(row["text_id"])
            if reference is None:
                continue
            for pointer_offset in reference["reference_offsets"]:
                for start, end, slot_id in intervals:
                    if pointer_offset < end and pointer_offset + 4 > start:
                        pointer_inside_slot.append(
                            {
                                "text_id": row["text_id"],
                                "pointer_offset": pointer_offset,
                                "slot_text_id": slot_id,
                            }
                        )

        assignments, failures = sorted_capacity_assignment(component_rows)
        packed_placements, packing_failures = pack_multiple_records(component_rows)
        clusters, borrowed_zero_bytes = merge_zero_aligned_slots(
            data, intervals, pointer_ranges
        )
        cluster_placements, cluster_failures = pack_records_into_regions(
            component_rows, clusters
        )
        for assignment in assignments:
            assignment["component"] = component
        all_assignments.extend(assignments)
        component_reports.append(
            {
                "component": component,
                "path": str(path),
                "sha256": sha256(path),
                "load_address": f"0x{base:08X}",
                "record_count": len(component_rows),
                "source_capacity_bytes": sum(row["source_capacity"] for row in component_rows),
                "translation_bytes": sum(row["translation_length"] for row in component_rows),
                "delta_bytes": sum(row["translation_length"] - row["source_capacity"] for row in component_rows),
                "max_source_capacity": max(row["source_capacity"] for row in component_rows),
                "max_translation_length": max(row["translation_length"] for row in component_rows),
                "individual_overflow_count": sum(
                    row["translation_length"] > row["source_capacity"] for row in component_rows
                ),
                "sorted_slot_assignment_failure_count": len(failures),
                "existing_slot_permutation_feasible": not failures,
                "assignment_move_count": sum(not row["stays_in_original_slot"] for row in assignments),
                "assignment_failures": failures,
                "multi_record_slot_packing_feasible": not packing_failures,
                "packed_placement_count": len(packed_placements),
                "packing_failures": packing_failures,
                "zero_aligned_cluster_count": len(clusters),
                "borrowed_zero_alignment_bytes": borrowed_zero_bytes,
                "zero_aligned_cluster_packing_feasible": not cluster_failures,
                "cluster_placement_count": len(cluster_placements),
                "cluster_packing_failures": cluster_failures,
            }
        )

    static_integrity_pass = not (
        unresolved_encoding
        or source_byte_mismatches
        or terminator_mismatches
        or overlapping_slots
        or pointer_mismatches
        or pointer_inside_slot
        or missing_references
    )
    all_components_feasible = all(
        row["existing_slot_permutation_feasible"] for row in component_reports
    )
    all_components_packable = all(
        row["multi_record_slot_packing_feasible"] for row in component_reports
    )
    all_components_cluster_packable = all(
        row["zero_aligned_cluster_packing_feasible"] for row in component_reports
    )
    return {
        "schema_version": 1,
        "status": "existing_slot_relocation_analysis_complete",
        "translated_ssot": {
            "path": str(translated_csv),
            "sha256": sha256(translated_csv),
            "read_only_analysis": True,
        },
        "audit_report": {
            "path": str(audit_report_path),
            "translated_sha256": audit_report["translated_ssot"]["sha256"],
        },
        "scope": {
            "populations": sorted(ACCEPTED_POPULATIONS),
            "record_count": len(rows),
            "component_count": len(component_reports),
            "direct_reference_record_count": len(reference_by_id),
        },
        "encoding_alias_candidate": {
            "mapping": ENCODING_ALIAS_CANDIDATES,
            "usage_count": len(alias_usage),
            "usage": alias_usage,
            "status": "build_layer_candidate_not_written_to_ssot",
            "unresolved_after_alias_count": len(unresolved_encoding),
            "unresolved_after_alias": unresolved_encoding,
        },
        "static_source_checks": {
            "source_byte_mismatch_count": len(source_byte_mismatches),
            "nul_terminator_mismatch_count": len(terminator_mismatches),
            "overlapping_slot_count": len(overlapping_slots),
            "missing_reference_count": len(missing_references),
            "pointer_mismatch_count": len(pointer_mismatches),
            "pointer_inside_slot_count": len(pointer_inside_slot),
            "pass": static_integrity_pass,
            "source_byte_mismatches": source_byte_mismatches,
            "nul_terminator_mismatches": terminator_mismatches,
            "overlapping_slots": overlapping_slots,
            "missing_references": missing_references,
            "pointer_mismatches": pointer_mismatches,
            "pointers_inside_slots": pointer_inside_slot,
        },
        "capacity": {
            "all_components_existing_slot_permutation_feasible": all_components_feasible,
            "feasible_component_count": sum(
                row["existing_slot_permutation_feasible"] for row in component_reports
            ),
            "infeasible_component_count": sum(
                not row["existing_slot_permutation_feasible"] for row in component_reports
            ),
            "total_source_capacity_bytes": sum(
                row["source_capacity_bytes"] for row in component_reports
            ),
            "total_translation_bytes": sum(
                row["translation_bytes"] for row in component_reports
            ),
            "total_delta_bytes": sum(row["delta_bytes"] for row in component_reports),
            "total_individual_overflow_count": sum(
                row["individual_overflow_count"] for row in component_reports
            ),
            "total_assignment_move_count": sum(
                row["assignment_move_count"] for row in component_reports
            ),
            "all_components_multi_record_slot_packing_feasible": all_components_packable,
            "multi_record_packable_component_count": sum(
                row["multi_record_slot_packing_feasible"] for row in component_reports
            ),
            "multi_record_unpacked_component_count": sum(
                not row["multi_record_slot_packing_feasible"] for row in component_reports
            ),
            "all_components_zero_aligned_cluster_packing_feasible": all_components_cluster_packable,
            "zero_aligned_cluster_packable_component_count": sum(
                row["zero_aligned_cluster_packing_feasible"] for row in component_reports
            ),
            "zero_aligned_cluster_unpacked_component_count": sum(
                not row["zero_aligned_cluster_packing_feasible"] for row in component_reports
            ),
            "total_borrowed_zero_alignment_bytes": sum(
                row["borrowed_zero_alignment_bytes"] for row in component_reports
            ),
        },
        "components": component_reports,
        "assignment": all_assignments,
        "gates": {
            "source_and_reference_static_integrity": "pass" if static_integrity_pass else "fail",
            "existing_slot_capacity": "pass" if all_components_feasible else "fail",
            "multi_record_slot_capacity": "pass" if all_components_packable else "partial",
            "zero_aligned_cluster_capacity": "pass" if all_components_cluster_packable else "partial",
            "padding_policy": "runtime_poc_required",
            "auxiliary_consumer_coverage": "runtime_qa_required",
            "production_reinsertion": "pending_writer_and_runtime_poc",
        },
        "limitations": [
            "The assignment only proves byte capacity and absolute-pointer rewritability.",
            "Zero padding after a shorter NUL-terminated record still needs a representative runtime PoC.",
            "Auxiliary records have direct references but their full consumer and layout behavior is not runtime-confirmed.",
            "Font code allocation for the full 824-syllable demand is a separate gate.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--translated", type=Path, required=True)
    parser.add_argument("--audit-report", type=Path, required=True)
    parser.add_argument("--reference-report", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--extracted-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = analyze(
        args.translated,
        args.audit_report,
        args.reference_report,
        args.inventory,
        args.extracted_root,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "scope": report["scope"],
                "encoding_alias_candidate": report["encoding_alias_candidate"],
                "static_source_checks": {
                    key: value
                    for key, value in report["static_source_checks"].items()
                    if not isinstance(value, list)
                },
                "capacity": report["capacity"],
                "gates": report["gates"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
