#!/usr/bin/env python3
"""Extract reversible CP932/NUL text candidates from ARM9 and ARM9 overlays."""

from __future__ import annotations

import argparse
import json
import re
import struct
from collections import Counter
from pathlib import Path


DIRECTIVE = re.compile(r"%([A-Za-z])(?:\(([^)]*)\))?")


def has_japanese(text: str) -> bool:
    return any(
        "\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff"
        for char in text
    )


def segments(data: bytes):
    start = 0
    while start < len(data):
        end = data.find(b"\0", start)
        if end < 0:
            end = len(data)
        if end > start:
            yield start, data[start:end]
        start = end + 1


def trim_binary_prefix(offset: int, raw: bytes, text: str) -> tuple[int, bytes, str]:
    """Trim an unterminated pointer-table prefix before a terminal script record."""
    if not text.endswith("%e"):
        return offset, raw, text
    bad = [
        index
        for index, char in enumerate(text)
        if ord(char) < 0x20 or 0xE000 <= ord(char) <= 0xF8FF
    ]
    if not bad:
        return offset, raw, text
    match = DIRECTIVE.search(text, bad[-1] + 1)
    if match is None:
        return offset, raw, text
    prefix_size = len(text[: match.start()].encode("cp932"))
    return offset + prefix_size, raw[prefix_size:], text[match.start() :]


def extract_component(component: str, path: Path, include_weak: bool = False) -> list[dict]:
    data = path.read_bytes()
    rows = []
    for offset, raw in segments(data):
        if len(raw) < 2 or len(raw) > 0x10000:
            continue
        try:
            text = raw.decode("cp932")
        except UnicodeDecodeError:
            continue
        offset, raw, text = trim_binary_prefix(offset, raw, text)
        directives = [match.group(0) for match in DIRECTIVE.finditer(text)]
        if text.endswith("%e") and directives:
            kind = "script_record"
            confidence = "strong_static_candidate"
        elif include_weak and has_japanese(text):
            kind = "nul_text"
            confidence = "candidate"
        else:
            continue
        rows.append(
            {
                "text_id": f"{component}:{offset:06X}",
                "component": component,
                "offset": offset,
                "length": len(raw),
                "kind": kind,
                "confidence": confidence,
                "cp932": text,
                "raw_hex": raw.hex().upper(),
                "roundtrip_equal": text.encode("cp932") == raw,
                "directives": directives,
            }
        )
    return rows


def extract_pointer_targeted_records(
    component: str, path: Path, load_address: int, existing_rows: list[dict]
) -> list[dict]:
    """Recover terminal scripts whose start follows binary data without a NUL."""
    data = path.read_bytes()
    covered = [
        (row["offset"], row["offset"] + row["length"])
        for row in existing_rows
    ]
    targets: dict[int, list[int]] = {}
    # Every already-proved direct text reference in this title is word-aligned.
    # Restricting discovery to that ABI avoids accidental byte-shifted pointers.
    for pointer_offset in range(0, len(data) - 3, 4):
        value = struct.unpack_from("<I", data, pointer_offset)[0]
        target = value - load_address
        if 0 <= target < len(data):
            targets.setdefault(target, []).append(pointer_offset)
    rows = []
    for offset, pointer_offsets in sorted(targets.items()):
        if any(start <= offset < end for start, end in covered):
            continue
        end = data.find(b"\0", offset, min(len(data), offset + 0x10000))
        if end < 0 or end == offset:
            continue
        raw = data[offset:end]
        try:
            text = raw.decode("cp932")
        except UnicodeDecodeError:
            continue
        if any(ord(char) < 0x20 or 0xE000 <= ord(char) <= 0xF8FF for char in text):
            continue
        directives = [match.group(0) for match in DIRECTIVE.finditer(text)]
        if not text.endswith("%e") or not directives or not has_japanese(text):
            continue
        rows.append(
            {
                "text_id": f"{component}:{offset:06X}",
                "component": component,
                "offset": offset,
                "length": len(raw),
                "kind": "script_record",
                "confidence": "strong_pointer_targeted_candidate",
                "cp932": text,
                "raw_hex": raw.hex().upper(),
                "roundtrip_equal": text.encode("cp932") == raw,
                "directives": directives,
                "discovery_pointer_offsets": pointer_offsets,
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extracted-root", type=Path, required=True)
    parser.add_argument("--inventory-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--include-weak", action="store_true")
    args = parser.parse_args()
    inventory = json.loads(args.inventory_report.read_text(encoding="utf-8"))
    inputs = [
        (
            "arm9",
            args.extracted_root / "arm9" / "arm9.bin",
            inventory["header"]["arm9"]["load_address"],
        )
    ]
    inputs.extend(
        (
            f"arm9_overlay_{overlay['overlay_id']:03d}_file_{overlay['file_id']:03d}",
            args.extracted_root
            / "overlays"
            / f"arm9_overlay_{overlay['overlay_id']:03d}_file_{overlay['file_id']:03d}.bin",
            overlay["ram_address"],
        )
        for overlay in inventory["arm9_overlays"]
    )
    rows = []
    pointer_recovered_count = 0
    for name, path, load_address in inputs:
        component_rows = extract_component(name, path, include_weak=args.include_weak)
        recovered = extract_pointer_targeted_records(
            name, path, load_address, component_rows
        )
        pointer_recovered_count += len(recovered)
        rows.extend(component_rows + recovered)
    rows.sort(key=lambda row: (row["component"], row["offset"]))
    control_counts = Counter(
        directive for row in rows for directive in row["directives"]
    )
    component_counts = Counter(row["component"] for row in rows)
    report = {
        "schema_version": 1,
        "scope": "CP932 ARM9/overlay terminal-%e records recovered by NUL boundaries or direct in-component pointer targets",
        "completion_status": "candidate_population_not_runtime_confirmed",
        "limitations": [
            "Records in game-specific NitroFS archives are not included.",
            "NUL boundaries and directive semantics still require reader/runtime confirmation.",
            "UI strings without terminal %e are excluded unless --include-weak is requested and they contain Japanese text.",
        ],
        "record_count": len(rows),
        "script_record_count": sum(row["kind"] == "script_record" for row in rows),
        "nul_text_count": sum(row["kind"] == "nul_text" for row in rows),
        "roundtrip_equal_count": sum(row["roundtrip_equal"] for row in rows),
        "pointer_target_recovered_count": pointer_recovered_count,
        "component_counts": dict(sorted(component_counts.items())),
        "directive_counts": [
            {"token": token, "count": count}
            for token, count in sorted(control_counts.items())
        ],
        "records": rows,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
