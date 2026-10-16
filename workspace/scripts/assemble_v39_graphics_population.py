#!/usr/bin/env python3
"""Add the approved GPU3D myst03_tex restart button to the v38 population."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil

from PIL import Image


INPUT_CANVAS_COUNT = 64
OUTPUT_CANVAS_COUNT = 65
CANVAS_ID = "myst03_tex_restart_ko"
RESOURCE_ID = "sys_bg/myst03_tex"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-config", type=Path, required=True)
    parser.add_argument("--base-indexed", type=Path, required=True)
    parser.add_argument("--restart-indexed", type=Path, required=True)
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
    if len(rows) != INPUT_CANVAS_COUNT:
        raise ValueError(f"unexpected base canvas count: {len(rows)}")
    if any(row["canvas_id"] == CANVAS_ID for row in rows):
        raise ValueError(f"base population already contains {CANVAS_ID}")
    if any(row["resource_id"] == RESOURCE_ID for row in rows):
        raise ValueError(f"base population already contains {RESOURCE_ID}")

    with Image.open(args.restart_indexed) as image:
        if image.mode != "P" or image.size != (32, 32):
            raise ValueError("approved myst03_tex candidate must be indexed 32x32 PNG")

    new_row = {
        "canvas_id": CANVAS_ID,
        "resource_id": RESOURCE_ID,
        "shift_mode": "raster_rows",
        "shift_x": "0",
        "crop_x": "0",
        "crop_y": "0",
        "crop_w": "32",
        "crop_h": "32",
        "translation_target": "처음부터",
        "evidence": "user-approved r3 exact GPU3D linear texture trace pid1188",
        "status": "editing_crop_runtime_linear_flip_y_corrected",
        "notes": "상하 반전 저장, 일본어 회색 잔재 제거, 26x7 중앙 정렬",
    }
    if set(new_row) != set(header):
        raise ValueError("new row does not match base CSV schema")

    args.output_config.parent.mkdir(parents=True, exist_ok=True)
    args.output_indexed.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, str]] = []
    with args.output_config.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for row in rows:
            source = args.base_indexed / f"{row['canvas_id']}.png"
            target = args.output_indexed / source.name
            shutil.copyfile(source, target)
            writer.writerow(row)
            copied.append(
                {
                    "canvas_id": row["canvas_id"],
                    "resource_id": row["resource_id"],
                    "source_sha256": sha256(source),
                    "output_sha256": sha256(target),
                }
            )
        target = args.output_indexed / f"{CANVAS_ID}.png"
        shutil.copyfile(args.restart_indexed, target)
        writer.writerow(new_row)
        copied.append(
            {
                "canvas_id": CANVAS_ID,
                "resource_id": RESOURCE_ID,
                "source_sha256": sha256(args.restart_indexed),
                "output_sha256": sha256(target),
            }
        )

    population = list(args.output_indexed.glob("*.png"))
    if len(population) != OUTPUT_CANVAS_COUNT:
        raise ValueError(f"unexpected output PNG count: {len(population)}")
    output_rows = rows + [new_row]
    report = {
        "schema_version": 1,
        "status": "v39_cumulative_graphics_population_assembled",
        "inputs": {
            "base_config": {"path": str(args.base_config), "sha256": sha256(args.base_config)},
            "base_indexed": str(args.base_indexed),
            "restart_indexed": {
                "path": str(args.restart_indexed),
                "sha256": sha256(args.restart_indexed),
            },
        },
        "selection": {
            "canvas_count": len(output_rows),
            "unique_resource_count": len({row["resource_id"] for row in output_rows}),
            "added_canvas_id": CANVAS_ID,
            "added_resource_id": RESOURCE_ID,
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
