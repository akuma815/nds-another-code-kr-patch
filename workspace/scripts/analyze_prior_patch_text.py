#!/usr/bin/env python3
"""Catalogue changed NUL-terminated text records in a same-layout prior ROM."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


DIRECTIVE = re.compile(rb"%[A-Za-z](?:\([^\x00)]*\))?")


def enclosing_record(data: bytes, offset: int, limit: int = 0x4000) -> tuple[int, int]:
    start = data.rfind(b"\0", max(0, offset - limit), offset) + 1
    end = data.find(b"\0", offset, min(len(data), offset + limit))
    if end < 0:
        end = min(len(data), offset + limit)
    return start, end


def changed_records(base: bytes, candidate: bytes) -> list[tuple[int, int]]:
    if len(base) != len(candidate):
        raise ValueError("This catalogue requires same-size component inputs")
    records = set()
    for offset, (left, right) in enumerate(zip(base, candidate)):
        if left != right:
            records.add(enclosing_record(base, offset))
    return sorted(records)


def decode_cp932(data: bytes) -> tuple[str, bool]:
    try:
        return data.decode("cp932"), True
    except UnicodeDecodeError:
        return data.decode("cp932", "replace"), False


def directives(data: bytes) -> list[str]:
    return [match.group().decode("ascii", "replace") for match in DIRECTIVE.finditer(data)]


def analyse_component(name: str, base_path: Path, candidate_path: Path) -> dict:
    base = base_path.read_bytes()
    candidate = candidate_path.read_bytes()
    rows = []
    for record_id, (start, end) in enumerate(changed_records(base, candidate)):
        left = base[start:end]
        right = candidate[start:end]
        left_text, left_valid = decode_cp932(left)
        right_text, right_valid = decode_cp932(right)
        left_directives = directives(left)
        right_directives = directives(right)
        rows.append(
            {
                "record_id": f"{name}:{start:06X}",
                "component": name,
                "offset": start,
                "length": end - start,
                "base_hex": left.hex().upper(),
                "candidate_hex": right.hex().upper(),
                "base_cp932": left_text,
                "base_cp932_valid": left_valid,
                "candidate_cp932": right_text,
                "candidate_cp932_valid": right_valid,
                "base_directives": left_directives,
                "candidate_directives": right_directives,
                "directive_sequence_equal": left_directives == right_directives,
            }
        )
    return {"component": name, "record_count": len(rows), "records": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    pairs = [("arm9", Path("arm9/arm9.bin"))]
    for base_overlay in sorted((args.base_root / "overlays").glob("*.bin")):
        relative = base_overlay.relative_to(args.base_root)
        candidate = args.candidate_root / relative
        if candidate.exists() and base_overlay.read_bytes() != candidate.read_bytes():
            pairs.append((base_overlay.stem, relative))
    components = [
        analyse_component(name, args.base_root / relative, args.candidate_root / relative)
        for name, relative in pairs
    ]
    records = [row for component in components for row in component["records"]]
    report = {
        "schema_version": 1,
        "scope": "same-size NUL-terminated records enclosing bytes changed by the prior J-K ROM",
        "limitations": [
            "Candidate Korean bytes are custom font-slot codes and are not decoded as Korean here.",
            "NUL-boundary grouping is a structural candidate until the runtime reader is traced.",
            "Unchanged text records are outside this report's population.",
        ],
        "component_count": len(components),
        "record_count": len(records),
        "base_cp932_valid_count": sum(row["base_cp932_valid"] for row in records),
        "directive_sequence_equal_count": sum(row["directive_sequence_equal"] for row in records),
        "components": components,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
