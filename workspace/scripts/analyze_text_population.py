#!/usr/bin/env python3
"""Audit the open text population and directive grammar without claiming completeness."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from extract_script_candidates import DIRECTIVE, extract_component


KANA = re.compile(r"[\u3040-\u30ff]")


def is_table_string(row: dict) -> bool:
    """Conservative candidate for a NUL-delimited CP932 label/table entry."""
    if row["kind"] != "nul_text" or len(row["cp932"]) < 2:
        return False
    for char in row["cp932"]:
        code = ord(char)
        if code < 0x20 or 0xE000 <= code <= 0xF8FF or 0xFF61 <= code <= 0xFF9F:
            return False
        if len(char.encode("cp932")) != 2:
            return False
    return True


def group_nearby(rows: list[dict], max_gap: int = 32) -> list[list[dict]]:
    groups: list[list[dict]] = []
    current: list[dict] = []
    previous = None
    for row in sorted(rows, key=lambda item: item["offset"]):
        gap = None if previous is None else row["offset"] - (
            previous["offset"] + previous["length"]
        )
        if gap is None or gap <= max_gap:
            current.append(row)
        else:
            groups.append(current)
            current = [row]
        previous = row
    if current:
        groups.append(current)
    return groups


def is_candidate_table(group: list[dict]) -> bool:
    if len(group) >= 3:
        return True
    return len(group) == 2 and all(KANA.search(row["cp932"]) for row in group)


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    size = len(data)
    return -sum((count / size) * math.log2(count / size) for count in counts.values())


def directive_shape(argument: str | None) -> str:
    if argument is None:
        return "bare"
    if argument.isdigit():
        return "digits"
    if re.fullmatch(r"[A-Z]\d+", argument):
        return "upper_id"
    if re.fullmatch(r"\d+_\d+", argument):
        return "pair_digits"
    return "free_text"


def control_audit(records: list[dict]) -> dict:
    token_counts: dict[str, Counter] = defaultdict(Counter)
    token_examples: dict[str, list[dict]] = defaultdict(list)
    unmatched_percent = []
    literal_counts = Counter()
    terminal_count = 0
    for row in records:
        text = row["cp932"]
        covered = [False] * len(text)
        for match in DIRECTIVE.finditer(text):
            token = f"%{match.group(1)}"
            argument = match.group(2)
            token_counts[token]["count"] += 1
            token_counts[token][directive_shape(argument)] += 1
            if len(token_examples[token]) < 3:
                token_examples[token].append(
                    {"text_id": row["text_id"], "raw": match.group(0), "argument": argument}
                )
            for index in range(match.start(), match.end()):
                covered[index] = True
        for index, char in enumerate(text):
            if char == "%" and not covered[index]:
                unmatched_percent.append({"text_id": row["text_id"], "index": index})
            if not covered[index]:
                literal_counts[char] += 1
        terminal_count += text.endswith("%e")

    token_policies = {
        "%e": {
            "boundary": "bare token; final token in every strong record",
            "meaning": "record terminator",
            "confidence": "structurally_confirmed",
            "policy": "preserve",
        },
        "%h": {
            "boundary": "bare token",
            "meaning": "page/wait progression observed in the font PoC scene",
            "confidence": "runtime_observed_single_path",
            "policy": "preserve",
        },
        "%r": {
            "boundary": "bare token",
            "meaning": "line-layout transition hypothesis",
            "confidence": "static_hypothesis",
            "policy": "forbidden",
        },
    }
    tokens = []
    for token in sorted(token_counts):
        policy = token_policies.get(
            token,
            {
                "boundary": "syntax boundary parsed; argument shape recorded below",
                "meaning": "not confirmed",
                "confidence": "boundary_only",
                "policy": "forbidden",
            },
        )
        tokens.append(
            {
                "token": token,
                **dict(sorted(token_counts[token].items())),
                **policy,
                "examples": token_examples[token],
            }
        )
    return {
        "schema_version": 1,
        "scope": "directives in the terminal-%e ARM9/overlay script candidates",
        "record_count": len(records),
        "records_ending_e": terminal_count,
        "unmatched_percent_count": len(unmatched_percent),
        "unmatched_percent": unmatched_percent,
        "literal_unique_character_count": len(literal_counts),
        "literal_character_counts": [
            {"character": char, "count": count}
            for char, count in sorted(literal_counts.items(), key=lambda item: ord(item[0]))
        ],
        "tokens": tokens,
        "warning": "Token meanings other than the explicitly observed/confirmed subset are not translation-safe.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extracted-root", type=Path, required=True)
    parser.add_argument("--script-report", type=Path, required=True)
    parser.add_argument("--population-report", type=Path, required=True)
    parser.add_argument("--aux-report", type=Path, required=True)
    parser.add_argument("--control-report", type=Path, required=True)
    args = parser.parse_args()

    script_report = json.loads(args.script_report.read_text(encoding="utf-8"))
    strong_records = script_report["records"]
    inputs = [
        ("arm9", args.extracted_root / "arm9" / "arm9.bin"),
        ("arm7", args.extracted_root / "arm7" / "arm7.bin"),
    ]
    inputs.extend(
        (path.stem, path) for path in sorted((args.extracted_root / "overlays").glob("*.bin"))
    )

    auxiliary_records = []
    tables = []
    unresolved_clean_records = []
    for component, path in inputs:
        rows = extract_component(component, path, include_weak=True)
        candidates = [row for row in rows if is_table_string(row)]
        accepted_ids = set()
        for group in group_nearby(candidates):
            if not is_candidate_table(group):
                continue
            table_id = f"{component}:table:{group[0]['offset']:06X}"
            tables.append(
                {
                    "table_id": table_id,
                    "component": component,
                    "start_offset": group[0]["offset"],
                    "end_offset": group[-1]["offset"] + group[-1]["length"],
                    "record_count": len(group),
                    "confidence": "clustered_cp932_candidate_not_runtime_confirmed",
                }
            )
            for row in group:
                accepted_ids.add(row["text_id"])
                auxiliary_records.append(
                    {
                        **row,
                        "table_id": table_id,
                        "confidence": "clustered_cp932_candidate_not_runtime_confirmed",
                    }
                )
        unresolved_clean_records.extend(
            {
                **row,
                "confidence": "isolated_clean_cp932_candidate_not_runtime_confirmed",
            }
            for row in candidates
            if row["text_id"] not in accepted_ids
        )

    pack_rows = []
    pack_root = args.extracted_root / "nitrofs" / "data" / "pack"
    for path in sorted(pack_root.glob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        raw_strong = extract_component(path.name, path, include_weak=False)
        pack_rows.append(
            {
                "path": str(path.relative_to(args.extracted_root)).replace("\\", "/"),
                "size": len(data),
                "entropy": round(entropy(data), 4),
                "raw_percent_e_candidate_count": len(raw_strong),
                "classification": "opaque_resource_binary_raw_text_hits_not_accepted",
            }
        )

    control_report = control_audit(strong_records)
    auxiliary_report = {
        "schema_version": 1,
        "scope": "clustered clean double-byte CP932 NUL strings in ARM9/ARM7/ARM9 overlays",
        "completion_status": "candidate_tables_not_runtime_confirmed",
        "heuristic": {
            "max_inter_record_gap": 32,
            "minimum_cluster_size": 3,
            "two_record_exception": "both records contain kana",
            "character_filter": "all characters encode to two-byte CP932; C0, PUA, and halfwidth kana rejected",
        },
        "table_count": len(tables),
        "record_count": len(auxiliary_records),
        "roundtrip_equal_count": sum(row["roundtrip_equal"] for row in auxiliary_records),
        "tables": tables,
        "records": auxiliary_records,
    }
    population_report = {
        "schema_version": 1,
        "scope": "Japanese ANOJ ARM9/ARM7/overlay text candidates plus raw NitroFS pack audit",
        "completion_status": "open_population_not_complete",
        "confirmed_runtime_records": 1,
        "strong_script_candidates": len(strong_records),
        "clustered_auxiliary_candidates": len(auxiliary_records),
        "candidate_total_without_deduplication": len(strong_records) + len(auxiliary_records),
        "auxiliary_table_count": len(tables),
        "unresolved_isolated_clean_cp932_rows": len(unresolved_clean_records),
        "unresolved_isolated_records": unresolved_clean_records,
        "strong_roundtrip_equal_count": sum(row["roundtrip_equal"] for row in strong_records),
        "auxiliary_roundtrip_equal_count": sum(row["roundtrip_equal"] for row in auxiliary_records),
        "control_token_count": len(control_report["tokens"]),
        "control_records_ending_e": control_report["records_ending_e"],
        "control_unmatched_percent_count": control_report["unmatched_percent_count"],
        "pack_audit": pack_rows,
        "resolved_boundaries": [
            "The strong candidates are NUL-delimited, CP932-roundtrippable, directive-bearing records ending in %e.",
            "The font PoC runtime confirms one overlay-3 record reaches the main dialogue renderer.",
            "Clustered auxiliary tables preserve raw bytes and stable component/offset identities but remain candidates.",
        ],
        "not_resolved": [
            "Consumer/reference ownership for all strong and auxiliary candidates.",
            "Meaning and edit policy of most directive tokens.",
            "Isolated/singleton UI strings and any non-NUL text structures.",
            "Internal serialization/compression and possible text ownership of data/pack resource binaries.",
            "Baked text in graphics and separate renderer paths.",
        ],
        "next": "Trace references for one strong record and its adjacent auxiliary table, then build a one-record E2E reinsertion without relocating data.",
    }

    for path, report in (
        (args.population_report, population_report),
        (args.aux_report, auxiliary_report),
        (args.control_report, control_report),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
