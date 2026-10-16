#!/usr/bin/env python3
"""Assemble the v35 graphics population from verified v34 plus approved cur_u r4."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil

from PIL import Image


EXPECTED_CANVAS_COUNT = 63
PHOTO_CANVAS_ID = "pda_photo_up"
PHOTO_RESOURCE_ID = "pda/cur_u"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v34-config", type=Path, required=True)
    parser.add_argument("--v34-indexed", type=Path, required=True)
    parser.add_argument("--photo-up-indexed", type=Path, required=True)
    parser.add_argument("--output-config", type=Path, required=True)
    parser.add_argument("--output-indexed", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    for path in (args.output_config, args.report):
        if path.exists():
            raise FileExistsError(f"refusing existing output: {path}")
    if args.output_indexed.exists() and any(args.output_indexed.iterdir()):
        raise FileExistsError(f"refusing nonempty output directory: {args.output_indexed}")

    with args.v34_config.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("missing v34 CSV header")
        header = list(reader.fieldnames)
        rows = list(reader)
    if len(rows) != EXPECTED_CANVAS_COUNT:
        raise ValueError(f"unexpected v34 canvas count: {len(rows)}")
    photo_rows = [row for row in rows if row["canvas_id"] == PHOTO_CANVAS_ID]
    if len(photo_rows) != 1 or photo_rows[0]["resource_id"] != PHOTO_RESOURCE_ID:
        raise ValueError("v34 pda/cur_u row mismatch")

    with Image.open(args.photo_up_indexed) as image:
        if image.mode != "P" or image.size != (32, 32):
            raise ValueError("approved pda/cur_u candidate must be indexed 32x32 PNG")

    args.output_config.parent.mkdir(parents=True, exist_ok=True)
    args.output_indexed.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    copied = []
    with args.output_config.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for row in rows:
            source = args.v34_indexed / f"{row['canvas_id']}.png"
            if row["canvas_id"] == PHOTO_CANVAS_ID:
                source = args.photo_up_indexed
                row = dict(row)
                row["translation_target"] = "위로 이동"
                row["evidence"] = "user-approved r4 runtime-linear preview"
                row["status"] = "editing_crop_runtime_linear_corrected"
                row["notes"] = "일본어 잔재 제거, 중앙 정렬, 테두리를 기준 빨간색으로 통일한 r4"
            writer.writerow(row)
            target = args.output_indexed / f"{row['canvas_id']}.png"
            shutil.copyfile(source, target)
            copied.append({
                "canvas_id": row["canvas_id"],
                "resource_id": row["resource_id"],
                "source": str(source),
                "source_sha256": sha256(source),
                "output_sha256": sha256(target),
            })

    population = list(args.output_indexed.glob("*.png"))
    if len(population) != EXPECTED_CANVAS_COUNT:
        raise ValueError(f"unexpected output PNG count: {len(population)}")
    report = {
        "schema_version": 1,
        "status": "v35_cumulative_graphics_population_assembled",
        "inputs": {
            "v34_config": {"path": str(args.v34_config), "sha256": sha256(args.v34_config)},
            "v34_indexed": str(args.v34_indexed),
            "photo_up_indexed": {"path": str(args.photo_up_indexed), "sha256": sha256(args.photo_up_indexed)},
        },
        "selection": {
            "canvas_count": len(rows),
            "unique_resource_count": len({row["resource_id"] for row in rows}),
            "replaced_canvas_id": PHOTO_CANVAS_ID,
            "replaced_resource_id": PHOTO_RESOURCE_ID,
        },
        "outputs": {
            "config": {"path": str(args.output_config), "sha256": sha256(args.output_config)},
            "indexed_dir": str(args.output_indexed),
            "png_count": len(population),
        },
        "files": copied,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], **report["selection"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
