#!/usr/bin/env python3
"""Catalog direct in-component RAM pointers to extracted text records."""

from __future__ import annotations

import argparse
import json
import re
import struct
from collections import Counter
from pathlib import Path


OVERLAY_COMPONENT = re.compile(r"arm9_overlay_(\d{3})_file_(\d{3})$")


def find_all(data: bytes, needle: bytes) -> list[int]:
    offsets = []
    start = 0
    while True:
        offset = data.find(needle, start)
        if offset < 0:
            return offsets
        offsets.append(offset)
        start = offset + 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extracted-root", type=Path, required=True)
    parser.add_argument("--inventory-report", type=Path, required=True)
    parser.add_argument("--script-report", type=Path, required=True)
    parser.add_argument("--aux-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    inventory = json.loads(args.inventory_report.read_text(encoding="utf-8"))
    scripts = json.loads(args.script_report.read_text(encoding="utf-8"))["records"]
    auxiliary = json.loads(args.aux_report.read_text(encoding="utf-8"))["records"]
    loads = {"arm9": inventory["header"]["arm9"]["load_address"]}
    paths = {"arm9": args.extracted_root / "arm9" / "arm9.bin"}
    for overlay in inventory["arm9_overlays"]:
        component = (
            f"arm9_overlay_{overlay['overlay_id']:03d}_file_{overlay['file_id']:03d}"
        )
        loads[component] = overlay["ram_address"]
        paths[component] = args.extracted_root / "overlays" / f"{component}.bin"
    component_data = {component: path.read_bytes() for component, path in paths.items()}

    catalog = []
    distributions = {}
    for population, rows in (("strong_script", scripts), ("auxiliary", auxiliary)):
        distribution = Counter()
        for row in rows:
            component = row["component"]
            ram_address = loads[component] + row["offset"]
            pointer_raw = struct.pack("<I", ram_address)
            reference_offsets = find_all(component_data[component], pointer_raw)
            distribution[len(reference_offsets)] += 1
            catalog.append(
                {
                    "reference_id": f"{row['text_id']}:direct-ram",
                    "text_id": row["text_id"],
                    "population": population,
                    "component": component,
                    "text_offset": row["offset"],
                    "ram_address": ram_address,
                    "pointer_encoding": "u32le_absolute_ram",
                    "pointer_raw_hex": pointer_raw.hex().upper(),
                    "reference_count": len(reference_offsets),
                    "reference_offsets": reference_offsets,
                }
            )
        distributions[population] = {
            str(count): records for count, records in sorted(distribution.items())
        }

    zero_reference = [row for row in catalog if row["reference_count"] == 0]
    report = {
        "schema_version": 1,
        "scope": "direct u32le absolute-RAM pointers within the owning ARM9/overlay component",
        "completion_status": (
            "catalog_complete_for_current_populations"
            if not zero_reference
            else "unresolved_zero_reference_records"
        ),
        "source_sha256": inventory["source"]["sha256"],
        "record_count": len(catalog),
        "strong_script_count": len(scripts),
        "auxiliary_count": len(auxiliary),
        "reference_count_distribution": distributions,
        "zero_reference_count": len(zero_reference),
        "zero_reference_ids": [row["text_id"] for row in zero_reference],
        "catalog": catalog,
        "proved": [
            "Every current strong and auxiliary record has at least one direct pointer in its owning component.",
            "Record RAM addresses are component load address plus corrected component offset.",
        ] if not zero_reference else [],
        "not_proved": [
            "That no other pointer representation or runtime-generated reference exists.",
            "That current populations include every text consumer and non-NUL structure.",
            "That relocating or growing any record is safe without updating all loader and boundary rules.",
        ],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
