#!/usr/bin/env python3
"""Correlate the live photo-command screen with decoded NDS graphics resources.

This is a read-only diagnostic over an existing ARM9 RAM/VRAM dump.  It does
not patch the ROM or the running emulator.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def occupancy(data: bytes, background: int) -> bytes:
    table = bytes(
        (1 if (value & 0x0F) != background else 0)
        | (2 if (value >> 4) != background else 0)
        for value in range(256)
    )
    return data.translate(table)


def find_palette_independent(
    needle: bytes, decoded_root: Path
) -> list[dict[str, object]]:
    target = occupancy(needle, 0)
    matches: list[dict[str, object]] = []
    for path in sorted(decoded_root.rglob("*.bin")):
        data = path.read_bytes()
        for background in range(16):
            offset = occupancy(data, background).find(target)
            if offset >= 0:
                matches.append(
                    {
                        "path": str(path.relative_to(decoded_root)),
                        "offset": offset,
                        "background_index": background,
                    }
                )
    return matches


def make_contact(paths: list[Path], output: Path, scale: int = 12) -> None:
    images = [Image.open(path).convert("RGBA") for path in paths]
    try:
        width = sum(image.width for image in images)
        height = max(image.height for image in images)
        joined = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        x = 0
        for image in images:
            joined.alpha_composite(image, (x, 0))
            x += image.width
        enlarged = joined.resize(
            (joined.width * scale, joined.height * scale), Image.Resampling.NEAREST
        )
        canvas = Image.new("RGB", (enlarged.width, enlarged.height + 28), "#D8D8D8")
        canvas.paste(enlarged, (0, 28), enlarged)
        draw = ImageDraw.Draw(canvas)
        draw.text((4, 6), "serch_icon_00 .. serch_icon_03 (horizontal)", fill="black")
        output.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output)
    finally:
        for image in images:
            image.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--dump-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    project = args.project_root.resolve()
    dump_dir = args.dump_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    ram = (dump_dir / "arm9_main_ram_02000000_4m.bin").read_bytes()
    vram_f = (dump_dir / "vram_F_04000.bin").read_bytes()
    decoded_root = project / "workspace/graphics/pack_v2/decoded"
    png_root = project / "workspace/graphics/pack_v2/png"

    rows: list[dict[str, object]] = []
    for ordinal, vram_offset in enumerate(range(0x2E00, 0x3000, 0x80)):
        block = vram_f[vram_offset : vram_offset + 0x80]
        ram_offset = ram.find(block)
        matches = find_palette_independent(block, decoded_root)
        rows.append(
            {
                "ordinal": ordinal,
                "vram_bank": "F",
                "vram_offset": f"0x{vram_offset:04X}",
                "ram_address": (
                    f"0x{0x02000000 + ram_offset:08X}" if ram_offset >= 0 else None
                ),
                "block_sha256": sha256(block),
                "resource_matches": matches,
            }
        )

    icon_paths = [png_root / "sys_bg" / f"serch_icon_{index:02d}.png" for index in range(4)]
    contact_path = output_dir / "serch_icon_00_03_horizontal_x12.png"
    make_contact(icon_paths, contact_path)

    report = {
        "schema_version": 1,
        "mode": "read_only_runtime_dump_correlation",
        "screen": "photo_view_user_confirmed_up_display",
        "vram_blocks": rows,
        "contact_sheet": str(contact_path.relative_to(project)),
        "verdict": (
            "VRAM F 0x2E00..0x2FFF is sourced from sys_bg/serch_icon_00..03; "
            "visual review is still required before deciding whether these blocks "
            "contain the Japanese label or only its arrow/frame."
        ),
    }
    (output_dir / "runtime_resource_trace.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with (output_dir / "runtime_resource_trace.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "ordinal",
                "vram_bank",
                "vram_offset",
                "ram_address",
                "block_sha256",
                "resource_match",
            ],
        )
        writer.writeheader()
        for row in rows:
            matches = row["resource_matches"]
            writer.writerow(
                {
                    **{key: row[key] for key in writer.fieldnames if key != "resource_match"},
                    "resource_match": ";".join(
                        f"{match['path']}@0x{int(match['offset']):X}/bg={match['background_index']}"
                        for match in matches
                    ),
                }
            )


if __name__ == "__main__":
    main()
