#!/usr/bin/env python3
"""Assemble the approved v33 cumulative graphics config and indexed PNG set."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil

from PIL import Image


V32_EXCLUDED = {"pda_back_bg"}
V25_SELECTED = {"pda_inventory_actions", "system_yes", "system_no"}
V27_EXCLUDED = {"chapter1_number", "chapter1_title"}
EXPECTED_CANVAS_COUNT = 61
EXPECTED_WRONG_RESOURCES = {"pda/bg01", "pda/p02"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"missing CSV header: {path}")
        return list(reader.fieldnames), list(reader)


def copy_png(source: Path, target: Path, expected_size: tuple[int, int]) -> dict[str, object]:
    if not source.is_file():
        raise FileNotFoundError(source)
    with Image.open(source) as image:
        if image.mode != "P":
            raise ValueError(f"indexed PNG required: {source}")
        if image.size != expected_size:
            raise ValueError(f"unexpected PNG dimensions: {source}: {image.size}")
    shutil.copyfile(source, target)
    return {
        "source": str(source),
        "source_sha256": sha256(source),
        "output": str(target),
        "output_sha256": sha256(target),
        "size": list(expected_size),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v32-config", type=Path, required=True)
    parser.add_argument("--v32-indexed", type=Path, required=True)
    parser.add_argument("--v25-config", type=Path, required=True)
    parser.add_argument("--v25-indexed", type=Path, required=True)
    parser.add_argument("--v27-config", type=Path, required=True)
    parser.add_argument("--v27-indexed", type=Path, required=True)
    parser.add_argument("--myst00-indexed", type=Path, required=True)
    parser.add_argument("--output-config", type=Path, required=True)
    parser.add_argument("--output-indexed", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    for path in (args.output_config, args.report):
        if path.exists():
            raise FileExistsError(f"refusing existing output: {path}")
    if args.output_indexed.exists() and any(args.output_indexed.iterdir()):
        raise FileExistsError(f"refusing nonempty output directory: {args.output_indexed}")

    header32, rows32 = read_csv(args.v32_config)
    header25, rows25 = read_csv(args.v25_config)
    header27, rows27 = read_csv(args.v27_config)
    if header32 != header25 or header32 != header27:
        raise ValueError("graphics config CSV schemas differ")

    selected: list[tuple[dict[str, str], Path]] = []
    selected.extend(
        (row, args.v32_indexed / f"{row['canvas_id']}.png")
        for row in rows32
        if row["canvas_id"] not in V32_EXCLUDED
    )
    selected.extend(
        (row, args.v25_indexed / f"{row['canvas_id']}.png")
        for row in rows25
        if row["canvas_id"] in V25_SELECTED
    )
    selected.extend(
        (row, args.v27_indexed / f"{row['canvas_id']}.png")
        for row in rows27
        if row["canvas_id"] not in V27_EXCLUDED
    )
    myst_row = {
        "canvas_id": "myst00_bg_back_ko",
        "resource_id": "sys_bg/myst00_bg",
        "shift_mode": "raster_rows",
        "shift_x": "0",
        "crop_x": "0",
        "crop_y": "0",
        "crop_w": "32",
        "crop_h": "40",
        "translation_target": "뒤로",
        "evidence": "user runtime screenshot and exact live Engine-A OBJ/OAM 99 texture match",
        "status": "editing_crop_runtime_corrected",
        "notes": "v32 오인 대상 pda/bg01을 제외하고 실제 sys_bg/myst00_bg를 교체",
    }
    selected.append((myst_row, args.myst00_indexed))

    canvas_ids = [row["canvas_id"] for row, _ in selected]
    if len(selected) != EXPECTED_CANVAS_COUNT:
        raise ValueError(f"unexpected canvas count: {len(selected)}")
    if len(set(canvas_ids)) != len(canvas_ids):
        raise ValueError("duplicate canvas IDs in cumulative population")
    resources = {row["resource_id"] for row, _ in selected}
    wrong = resources & EXPECTED_WRONG_RESOURCES
    if wrong:
        raise ValueError(f"wrong runtime resource carried forward: {sorted(wrong)}")
    required = {"pda/cur_u", "pda/pda01", "sys_bg/sys_icon", "sys_bg/myst00_bg"}
    if not required <= resources:
        raise ValueError(f"required resources missing: {sorted(required - resources)}")

    args.output_config.parent.mkdir(parents=True, exist_ok=True)
    args.output_indexed.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, object]] = []
    with args.output_config.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header32)
        writer.writeheader()
        for row, source in selected:
            writer.writerow(row)
            size = (int(row["crop_w"]), int(row["crop_h"]))
            target = args.output_indexed / f"{row['canvas_id']}.png"
            entry = copy_png(source, target, size)
            entry.update({"canvas_id": row["canvas_id"], "resource_id": row["resource_id"]})
            copied.append(entry)

    png_population = {path.stem for path in args.output_indexed.glob("*.png")}
    if png_population != set(canvas_ids):
        raise ValueError("output PNG population does not match config")

    report = {
        "schema_version": 1,
        "status": "v33_cumulative_graphics_population_assembled",
        "inputs": {
            "v32_config": {"path": str(args.v32_config), "sha256": sha256(args.v32_config)},
            "v25_config": {"path": str(args.v25_config), "sha256": sha256(args.v25_config)},
            "v27_config": {"path": str(args.v27_config), "sha256": sha256(args.v27_config)},
            "myst00_indexed": {"path": str(args.myst00_indexed), "sha256": sha256(args.myst00_indexed)},
        },
        "selection": {
            "canvas_count": len(selected),
            "unique_resource_count": len(resources),
            "excluded_canvas_ids": sorted(V32_EXCLUDED | V27_EXCLUDED),
            "excluded_wrong_resources": sorted(EXPECTED_WRONG_RESOURCES),
            "required_resources": sorted(required),
        },
        "outputs": {
            "config": {"path": str(args.output_config), "sha256": sha256(args.output_config)},
            "indexed_dir": str(args.output_indexed),
            "png_count": len(png_population),
        },
        "files": copied,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], **report["selection"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
