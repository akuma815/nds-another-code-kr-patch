#!/usr/bin/env python3
"""Replace only the v37 myst00_tex candidate with the approved r4 Y-flipped asset."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil

from PIL import Image


CANVAS_COUNT = 64
CANVAS_ID = "myst00_tex_back_ko"
RESOURCE_ID = "sys_bg/myst00_tex"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-config", type=Path, required=True)
    parser.add_argument("--base-indexed", type=Path, required=True)
    parser.add_argument("--doll-back-indexed", type=Path, required=True)
    parser.add_argument("--output-config", type=Path, required=True)
    parser.add_argument("--output-indexed", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    for path in (args.output_config, args.report):
        if path.exists():
            raise FileExistsError(f"refusing existing output: {path}")
    if args.output_indexed.exists() and any(args.output_indexed.iterdir()):
        raise FileExistsError(f"refusing nonempty output directory: {args.output_indexed}")

    with args.base_config.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("missing base CSV header")
        header = list(reader.fieldnames)
        rows = list(reader)
    if len(rows) != CANVAS_COUNT:
        raise ValueError(f"unexpected base canvas count: {len(rows)}")
    targets = [row for row in rows if row["canvas_id"] == CANVAS_ID]
    if len(targets) != 1 or targets[0]["resource_id"] != RESOURCE_ID:
        raise ValueError("v37 myst00_tex target is missing or ambiguous")

    with Image.open(args.doll_back_indexed) as image:
        if image.mode != "P" or image.size != (32, 32):
            raise ValueError("approved myst00_tex candidate must be indexed 32x32 PNG")

    args.output_config.parent.mkdir(parents=True, exist_ok=True)
    args.output_indexed.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, str]] = []
    output_rows: list[dict[str, str]] = []
    with args.output_config.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for row in rows:
            output_row = dict(row)
            source = args.base_indexed / f"{row['canvas_id']}.png"
            if row["canvas_id"] == CANVAS_ID:
                source = args.doll_back_indexed
                output_row["evidence"] = "user-approved r4 live GPU3D vertical-flip correction"
                output_row["status"] = "editing_crop_runtime_linear_flip_y_corrected"
                output_row["notes"] = "상하 반전 저장, 일본어 회색 잔재 제거, 20x10 확대 중앙 정렬"
            target = args.output_indexed / f"{row['canvas_id']}.png"
            shutil.copyfile(source, target)
            writer.writerow(output_row)
            output_rows.append(output_row)
            copied.append(
                {
                    "canvas_id": row["canvas_id"],
                    "resource_id": row["resource_id"],
                    "source_sha256": sha256(source),
                    "output_sha256": sha256(target),
                    "replaced_for_v38": row["canvas_id"] == CANVAS_ID,
                }
            )

    population = list(args.output_indexed.glob("*.png"))
    if len(population) != CANVAS_COUNT:
        raise ValueError(f"unexpected output PNG count: {len(population)}")
    report = {
        "schema_version": 1,
        "status": "v38_cumulative_graphics_population_assembled",
        "inputs": {
            "base_config": {"path": str(args.base_config), "sha256": sha256(args.base_config)},
            "base_indexed": str(args.base_indexed),
            "doll_back_indexed": {
                "path": str(args.doll_back_indexed),
                "sha256": sha256(args.doll_back_indexed),
            },
        },
        "selection": {
            "canvas_count": len(output_rows),
            "unique_resource_count": len({row["resource_id"] for row in output_rows}),
            "replaced_canvas_id": CANVAS_ID,
            "replaced_resource_id": RESOURCE_ID,
            "storage_layout": "linear",
            "storage_transform": "flip_top_bottom",
        },
        "outputs": {
            "config": {"path": str(args.output_config), "sha256": sha256(args.output_config)},
            "indexed_dir": str(args.output_indexed),
            "png_count": len(population),
        },
        "files": copied,
    }
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": report["status"], **report["selection"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
