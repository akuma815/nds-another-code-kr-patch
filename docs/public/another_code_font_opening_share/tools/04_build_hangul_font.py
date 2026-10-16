#!/usr/bin/env python3
"""Insert mapped glyphs from a 16x16 precomposed-Hangul sheet into a 2bpp font."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image


GLYPH_SIZE = 64


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def pack_glyph(values: list[int]) -> bytes:
    if len(values) != 256 or any(not 0 <= value <= 3 for value in values):
        raise ValueError("glyph must contain 256 values in range 0..3")
    output = bytearray()
    for tile_y, tile_x in ((0, 0), (0, 8), (8, 0), (8, 8)):
        for y in range(8):
            for group in range(2):
                value = 0
                for pixel in range(4):
                    source = values[(tile_y + y) * 16 + tile_x + group * 4 + pixel]
                    value |= source << (6 - pixel * 2)
                output.append(value)
    if len(output) != GLYPH_SIZE:
        raise AssertionError("packed glyph is not 64 bytes")
    return bytes(output)


def extract_cell(sheet: Image.Image, char: str, foreground: int) -> bytes:
    index = ord(char) - 0xAC00
    columns = sheet.width // 16
    x, y = (index % columns) * 16, (index // columns) * 16
    if x + 16 > sheet.width or y + 16 > sheet.height:
        raise ValueError(f"sheet does not contain {char} at cell {index}")
    cell = sheet.crop((x, y, x + 16, y + 16)).convert("RGBA")
    colors = sheet.getcolors(maxcolors=sheet.width * sheet.height)
    if not colors:
        raise ValueError("unable to determine a dominant sheet background")
    background = max(colors, key=lambda item: item[0])[1]
    values = [
        foreground if (rgba := cell.getpixel((x, y)))[3] and rgba != background else 0
        for y in range(16)
        for x in range(16)
    ]
    if not any(values):
        raise ValueError(f"source glyph is empty: {char}")
    return pack_glyph(values)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-font", type=Path, required=True)
    parser.add_argument("--hangul-sheet", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--foreground", type=int, choices=range(1, 4), default=3)
    args = parser.parse_args()
    if args.output.resolve() == args.base_font.resolve():
        raise ValueError("output must not overwrite the base font")
    base = args.base_font.read_bytes()
    if not base or len(base) % GLYPH_SIZE:
        raise ValueError("base font size must be a nonzero multiple of 64")
    mapping = json.loads(args.mapping.read_text(encoding="utf-8-sig")).get("mappings")
    if not isinstance(mapping, list) or not mapping:
        raise ValueError("mapping JSON must contain a nonempty mappings list")
    with Image.open(args.hangul_sheet) as opened:
        sheet = opened.convert("RGBA")
    if sheet.width % 16 or sheet.height % 16 or sheet.width // 16 != 64:
        raise ValueError("Hangul sheet must use 16x16 cells and exactly 64 columns")
    if sheet.width // 16 * (sheet.height // 16) < 11172:
        raise ValueError("Hangul sheet does not contain all 11,172 precomposed syllable cells")
    output = bytearray(base)
    writes = []
    seen_chars: set[str] = set()
    seen_indices: set[int] = set()
    for number, row in enumerate(mapping, 1):
        char = str(row.get("character", ""))
        index = int(row.get("glyph_index"))
        if len(char) != 1 or not 0xAC00 <= ord(char) <= 0xD7A3:
            raise ValueError(f"row {number}: invalid Hangul character")
        if char in seen_chars or index in seen_indices:
            raise ValueError(f"row {number}: duplicate character or glyph index")
        seen_chars.add(char)
        seen_indices.add(index)
        start = index * GLYPH_SIZE
        if index < 0 or start + GLYPH_SIZE > len(output):
            raise ValueError(f"row {number}: glyph index exceeds font")
        replacement = extract_cell(sheet, char, args.foreground)
        original = bytes(output[start : start + GLYPH_SIZE])
        output[start : start + GLYPH_SIZE] = replacement
        writes.append({"character": char, "glyph_index": index, "original_sha256": sha256(original), "replacement_sha256": sha256(replacement)})
    output_bytes = bytes(output)
    if len(output_bytes) != len(base):
        raise AssertionError("font size changed")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output_bytes)
    report = {
        "schema_version": 1,
        "status": "static_font_build_pass_runtime_not_proved",
        "base_font": {"size": len(base), "sha256": sha256(base)},
        "output_font": {"size": len(output_bytes), "sha256": sha256(output_bytes)},
        "write_count": len(writes),
        "writes": writes,
        "warning": "This tool does not prove that mapped codes are unused or accepted by the target game's runtime renderer.",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "write_count": len(writes)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
