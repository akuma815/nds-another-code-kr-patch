#!/usr/bin/env python3
"""Audit Hangul, two-byte code, and glyph-index mappings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


GLYPH_SIZE = 64
TRAILS_PER_LEAD = 188


def code_to_index(code: int) -> int:
    lead, trail = code >> 8, code & 0xFF
    if 0x81 <= lead <= 0x9F:
        lead_index = lead - 0x81
    elif 0xE0 <= lead <= 0xEF:
        lead_index = lead - 0xC1
    else:
        raise ValueError(f"unsupported lead byte: {lead:02X}")
    if not 0x40 <= trail <= 0xFC or trail == 0x7F:
        raise ValueError(f"unsupported trail byte: {trail:02X}")
    return lead_index * TRAILS_PER_LEAD + trail - 0x40 - (1 if trail >= 0x80 else 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mapping", type=Path)
    parser.add_argument("--font", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.mapping.read_text(encoding="utf-8-sig"))
    mappings = source.get("mappings")
    if not isinstance(mappings, list) or not mappings:
        raise ValueError("mapping JSON must contain a nonempty mappings list")
    font = args.font.read_bytes() if args.font else None
    if font is not None and len(font) % GLYPH_SIZE:
        raise ValueError("font size is not divisible by 64")
    seen_chars: set[str] = set()
    seen_codes: set[int] = set()
    seen_indices: set[int] = set()
    errors: list[str] = []
    rows = []
    for number, row in enumerate(mappings, 1):
        char = str(row.get("character", ""))
        try:
            code = int(str(row.get("code", "")), 16)
            index = int(row.get("glyph_index"))
        except (TypeError, ValueError):
            errors.append(f"row {number}: invalid code or glyph_index")
            continue
        if len(char) != 1 or not 0xAC00 <= ord(char) <= 0xD7A3:
            errors.append(f"row {number}: character is not one precomposed Hangul syllable")
        if char in seen_chars:
            errors.append(f"row {number}: duplicate character {char}")
        if code in seen_codes:
            errors.append(f"row {number}: duplicate code {code:04X}")
        if index in seen_indices:
            errors.append(f"row {number}: duplicate glyph index {index}")
        seen_chars.add(char)
        seen_codes.add(code)
        seen_indices.add(index)
        try:
            calculated = code_to_index(code)
            if calculated != index:
                errors.append(f"row {number}: code {code:04X} calculates to {calculated}, not {index}")
        except ValueError as exc:
            calculated = None
            errors.append(f"row {number}: {exc}")
        state = "not_checked"
        if font is not None:
            start = index * GLYPH_SIZE
            if index < 0 or start + GLYPH_SIZE > len(font):
                errors.append(f"row {number}: glyph index {index} exceeds font")
                state = "out_of_range"
            else:
                state = "all_zero" if not any(font[start : start + GLYPH_SIZE]) else "nonzero"
        rows.append({"character": char, "code": f"{code:04X}", "glyph_index": index, "calculated_index": calculated, "original_glyph": state})
    report = {
        "schema_version": 1,
        "status": "pass" if not errors else "fail",
        "mapping_count": len(mappings),
        "errors": errors,
        "rows": rows,
        "warning": "A pass proves structural consistency only; it does not prove that the selected codes are unused or runtime-safe.",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "mapping_count": len(mappings), "error_count": len(errors)}, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
