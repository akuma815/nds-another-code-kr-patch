#!/usr/bin/env python3
"""Assemble the approved v34 cumulative graphics config and indexed PNG set."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil

from PIL import Image


V32_REPLACED = {"pda_back_bg", "pda_mark", "pda_photo_up"}
V25_SELECTED = {"system_yes", "system_no"}
V27_EXCLUDED = {"chapter1_number", "chapter1_title"}
R8_SELECTED = {"pda_back_bg", "pda_inventory_actions", "pda_mark", "system_inventory"}
EXPECTED_CANVAS_COUNT = 63


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


def row(**values: str) -> dict[str, str]:
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v32-config", type=Path, required=True)
    parser.add_argument("--v32-indexed", type=Path, required=True)
    parser.add_argument("--v25-config", type=Path, required=True)
    parser.add_argument("--v25-indexed", type=Path, required=True)
    parser.add_argument("--v27-config", type=Path, required=True)
    parser.add_argument("--v27-indexed", type=Path, required=True)
    parser.add_argument("--r8-indexed", type=Path, required=True)
    parser.add_argument("--photo-up-indexed", type=Path, required=True)
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
        (item, args.v32_indexed / f"{item['canvas_id']}.png")
        for item in rows32
        if item["canvas_id"] not in V32_REPLACED
    )
    selected.extend(
        (item, args.v25_indexed / f"{item['canvas_id']}.png")
        for item in rows25
        if item["canvas_id"] in V25_SELECTED
    )
    selected.extend(
        (item, args.v27_indexed / f"{item['canvas_id']}.png")
        for item in rows27
        if item["canvas_id"] not in V27_EXCLUDED
    )

    replacements = {
        "pda_back_bg": row(canvas_id="pda_back_bg", resource_id="pda/bg01", shift_mode="raster_rows", shift_x="0", crop_x="32", crop_y="136", crop_w="32", crop_h="32", translation_target="뒤로", evidence="user runtime screenshot and exact baked background button match", status="editing_crop_runtime_corrected", notes="팔레트 순서를 보존해 일본어 잔재와 회색 잔재를 제거한 최종 r8 이미지"),
        "pda_inventory_actions": row(canvas_id="pda_inventory_actions", resource_id="pda/pda01", shift_mode="raster_rows", shift_x="0", crop_x="0", crop_y="0", crop_w="64", crop_h="64", translation_target="소지품 / 지우기 / 듣기", evidence="user runtime screenshots and indexed preview", status="editing_crop_runtime_corrected", notes="세 항목을 중앙 정렬하고 일본어 및 영문 잔재를 제거한 최종 r8 이미지"),
        "pda_mark": row(canvas_id="pda_mark", resource_id="pda/mark", shift_mode="tiled8_sequence", shift_x="-24", crop_x="0", crop_y="0", crop_w="64", crop_h="64", translation_target="뒤로", evidence="user runtime screenshot and corrected palette-order extraction", status="editing_normalization_runtime_corrected", notes="팔레트 순서 오류로 검게 변한 상단 명령을 최종 r8 이미지로 교체"),
        "system_inventory": row(canvas_id="system_inventory", resource_id="sys_bg/sys_icon", shift_mode="raster_rows", shift_x="0", crop_x="200", crop_y="0", crop_w="32", crop_h="32", translation_target="소지품", evidence="user runtime screenshot and corrected full-atlas match", status="editing_crop_runtime_corrected", notes="좌우 일본어 잔재와 왼쪽 회색 픽셀을 제거하고 중앙 정렬한 최종 r8 이미지"),
    }
    for canvas_id in ("pda_back_bg", "pda_inventory_actions", "pda_mark", "system_inventory"):
        selected.append((replacements[canvas_id], args.r8_indexed / f"{canvas_id}.png"))

    selected.append((
        row(canvas_id="pda_photo_up", resource_id="pda/cur_u", shift_mode="raster_rows", shift_x="0", crop_x="0", crop_y="0", crop_w="32", crop_h="32", translation_target="위에 표시", evidence="user runtime screenshot and exact live GPU texture match", status="editing_crop_runtime_corrected", notes="실제 pda/cur_u 텍스처의 팔레트 순서를 보존한 최종 이미지"),
        args.photo_up_indexed,
    ))
    selected.append((
        row(canvas_id="myst00_bg_back_ko", resource_id="sys_bg/myst00_bg", shift_mode="raster_rows", shift_x="0", crop_x="0", crop_y="0", crop_w="32", crop_h="40", translation_target="뒤로", evidence="user runtime screenshot and exact live Engine-A OBJ/OAM 99 texture match", status="editing_crop_runtime_corrected", notes="사진 및 로드 계열에서 사용하는 실제 sys_bg/myst00_bg를 팔레트 순서 보존 이미지로 교체"),
        args.myst00_indexed,
    ))

    canvas_ids = [item["canvas_id"] for item, _ in selected]
    if len(selected) != EXPECTED_CANVAS_COUNT:
        raise ValueError(f"unexpected canvas count: {len(selected)}")
    if len(set(canvas_ids)) != len(canvas_ids):
        raise ValueError("duplicate canvas IDs in cumulative population")
    resources = {item["resource_id"] for item, _ in selected}
    required = {"pda/bg01", "pda/cur_u", "pda/mark", "pda/pda01", "sys_bg/sys_icon", "sys_bg/myst00_bg"}
    if not required <= resources:
        raise ValueError(f"required resources missing: {sorted(required - resources)}")
    if "pda/p02" in resources:
        raise ValueError("wrong runtime resource carried forward: pda/p02")

    args.output_config.parent.mkdir(parents=True, exist_ok=True)
    args.output_indexed.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, object]] = []
    with args.output_config.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header32)
        writer.writeheader()
        for item, source in selected:
            writer.writerow(item)
            size = (int(item["crop_w"]), int(item["crop_h"]))
            target = args.output_indexed / f"{item['canvas_id']}.png"
            entry = copy_png(source, target, size)
            entry.update({"canvas_id": item["canvas_id"], "resource_id": item["resource_id"]})
            copied.append(entry)

    png_population = {path.stem for path in args.output_indexed.glob("*.png")}
    if png_population != set(canvas_ids):
        raise ValueError("output PNG population does not match config")

    report = {
        "schema_version": 1,
        "status": "v34_cumulative_graphics_population_assembled",
        "inputs": {
            "v32_config": {"path": str(args.v32_config), "sha256": sha256(args.v32_config)},
            "v25_config": {"path": str(args.v25_config), "sha256": sha256(args.v25_config)},
            "v27_config": {"path": str(args.v27_config), "sha256": sha256(args.v27_config)},
            "r8_indexed_dir": str(args.r8_indexed),
            "photo_up_indexed": {"path": str(args.photo_up_indexed), "sha256": sha256(args.photo_up_indexed)},
            "myst00_indexed": {"path": str(args.myst00_indexed), "sha256": sha256(args.myst00_indexed)},
        },
        "selection": {
            "canvas_count": len(selected),
            "unique_resource_count": len(resources),
            "replaced_canvas_ids": sorted(V32_REPLACED | R8_SELECTED),
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
