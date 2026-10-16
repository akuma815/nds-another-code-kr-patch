#!/usr/bin/env python3
"""Build a minimal Expected-Write ROM proving the Galmuri font path."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image

from analyze_game_font import GLYPH_SIZE, sjis_to_index
from nds_inventory import NdsRom


SOURCE_FIRST = 0xAC00
SOURCE_LAST = 0xD7A3
FONT_PATH = "data/font_lc.bin"
OVERLAY_ID = 3
TEXT_OFFSET = 0x3028
EXPECTED_TEXT = bytes.fromhex("82 A8")  # お
POC_CODE = 0x96B7


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def source_cell_size(image_size: tuple[int, int]) -> int:
    if image_size == (512, 1400):
        return 8
    if image_size == (1024, 2800):
        return 16
    raise ValueError(f"Unexpected Galmuri sheet size: {image_size}")


def load_source_glyph(path: Path, char: str) -> list[list[int]]:
    if len(char) != 1 or not SOURCE_FIRST <= ord(char) <= SOURCE_LAST:
        raise ValueError("PoC character must be one precomposed Hangul syllable")
    with Image.open(path) as opened:
        image = opened.convert("RGBA")
    cell_size = source_cell_size(image.size)
    colors = image.getcolors(maxcolors=image.width * image.height)
    if not colors:
        raise ValueError("Unable to enumerate source colors")
    background = max(colors, key=lambda item: item[0])[1]
    index = ord(char) - SOURCE_FIRST
    columns = image.width // cell_size
    x0 = (index % columns) * cell_size
    y0 = (index // columns) * cell_size
    glyph = []
    for y in range(cell_size):
        row = []
        for x in range(cell_size):
            pixel = image.getpixel((x0 + x, y0 + y))
            row.append(3 if pixel[3] and pixel != background else 0)
        glyph.append(row)
    if not any(value for row in glyph for value in row):
        raise ValueError(f"Source glyph for {char} is empty")
    return glyph


def load_target_glyph(path: Path, char: str) -> list[list[int]]:
    glyph = load_source_glyph(path, char)
    if len(glyph) == 8:
        return scale_2x(glyph)
    if len(glyph) == 16:
        return glyph
    raise ValueError(f"Unsupported source glyph size: {len(glyph)}")


def scale_2x(glyph: list[list[int]]) -> list[list[int]]:
    return [
        [glyph[y // 2][x // 2] for x in range(16)]
        for y in range(16)
    ]


def pack_16x16_2bpp(glyph: list[list[int]]) -> bytes:
    if len(glyph) != 16 or any(len(row) != 16 for row in glyph):
        raise ValueError("Target glyph must be 16x16")
    output = bytearray()
    for tile_y, tile_x in ((0, 0), (0, 8), (8, 0), (8, 8)):
        for y in range(8):
            for group in range(2):
                byte = 0
                for pixel in range(4):
                    value = glyph[tile_y + y][tile_x + group * 4 + pixel]
                    if not 0 <= value <= 3:
                        raise ValueError("2bpp pixel is outside 0..3")
                    byte |= value << (6 - pixel * 2)
                output.append(byte)
    if len(output) != GLYPH_SIZE:
        raise AssertionError("Packed glyph size invariant failed")
    return bytes(output)


def build(source_rom: Path, font_png: Path, output_rom: Path, report_path: Path, char: str) -> None:
    rom = NdsRom(source_rom)
    font_file_id = next(
        (file_id for file_id, path in rom.paths.items() if path == FONT_PATH), None
    )
    if font_file_id is None:
        raise ValueError(f"NitroFS file not found: {FONT_PATH}")
    overlay = next(
        (row for row in rom.arm9_overlays if row["overlay_id"] == OVERLAY_ID), None
    )
    if overlay is None:
        raise ValueError(f"ARM9 overlay {OVERLAY_ID} not found")

    output = bytearray(rom.data)
    writes = []

    overlay_start, overlay_end = rom.fat[overlay["file_id"]]
    text_rom_offset = overlay_start + TEXT_OFFSET
    actual_text = bytes(output[text_rom_offset : text_rom_offset + len(EXPECTED_TEXT)])
    if actual_text != EXPECTED_TEXT:
        raise ValueError(
            f"Text expected bytes mismatch at 0x{text_rom_offset:X}: "
            f"expected {EXPECTED_TEXT.hex().upper()}, got {actual_text.hex().upper()}"
        )
    replacement_text = POC_CODE.to_bytes(2, "big")
    output[text_rom_offset : text_rom_offset + 2] = replacement_text
    writes.append(
        {
            "owner": "poc_text",
            "component": f"arm9_overlay_{OVERLAY_ID:03d}",
            "component_offset": TEXT_OFFSET,
            "rom_offset": text_rom_offset,
            "size": 2,
            "expected_hex": EXPECTED_TEXT.hex().upper(),
            "replacement_hex": replacement_text.hex().upper(),
        }
    )

    replacement_glyph = pack_16x16_2bpp(load_target_glyph(font_png, char))
    glyph_index = sjis_to_index(POC_CODE)
    font_start, font_end = rom.fat[font_file_id]
    glyph_rom_offset = font_start + glyph_index * GLYPH_SIZE
    if glyph_rom_offset + GLYPH_SIZE > font_end:
        raise ValueError("PoC glyph write exceeds font_lc.bin")
    original_glyph = bytes(output[glyph_rom_offset : glyph_rom_offset + GLYPH_SIZE])
    output[glyph_rom_offset : glyph_rom_offset + GLYPH_SIZE] = replacement_glyph
    writes.append(
        {
            "owner": "poc_font",
            "component": FONT_PATH,
            "file_id": font_file_id,
            "glyph_index": glyph_index,
            "sjis_code": f"{POC_CODE:04X}",
            "hangul": char,
            "source_cell_index": ord(char) - SOURCE_FIRST,
            "component_offset": glyph_index * GLYPH_SIZE,
            "rom_offset": glyph_rom_offset,
            "size": GLYPH_SIZE,
            "expected_sha256": sha256(original_glyph),
            "replacement_sha256": sha256(replacement_glyph),
        }
    )

    output_bytes = bytes(output)
    changed_offsets = [
        index for index, (left, right) in enumerate(zip(rom.data, output_bytes)) if left != right
    ]
    allowed = set(range(text_rom_offset, text_rom_offset + 2)) | set(
        range(glyph_rom_offset, glyph_rom_offset + GLYPH_SIZE)
    )
    if any(offset not in allowed for offset in changed_offsets):
        raise AssertionError("Output contains a change outside registered Expected Writes")
    output_rom.parent.mkdir(parents=True, exist_ok=True)
    output_rom.write_bytes(output_bytes)
    output_hash = sha256(output_bytes)
    preserved_runtime_result = None
    if report_path.exists():
        try:
            previous_report = json.loads(report_path.read_text(encoding="utf-8"))
            previous_output = previous_report.get("output", {})
            if (
                previous_report.get("status") == "runtime_pass"
                and previous_output.get("sha256") == output_hash
            ):
                preserved_runtime_result = previous_report.get("runtime_result")
        except (OSError, json.JSONDecodeError):
            preserved_runtime_result = None

    report = {
        "schema_version": 1,
        "gate_id": "font-visibility-001",
        "status": "static_build_pass_runtime_pending",
        "source": {"path": str(source_rom), "sha256": sha256(rom.data), "size": len(rom.data)},
        "font_source": {"path": str(font_png), "sha256": sha256(font_png.read_bytes())},
        "output": {"path": str(output_rom), "sha256": output_hash, "size": len(output_bytes)},
        "expected_screen_text": f"{char}い、着いたぜ！",
        "changed_byte_count": len(changed_offsets),
        "registered_write_byte_count": 66,
        "writes": writes,
        "runtime_criteria": {
            "pass": f"overlay 3 target line begins with a legible {char} and the rest of the line behaves normally",
            "fail": "boot/scene failure, wrong glyph, clipping that defeats readability, or adjacent glyph corruption",
            "not_proved": "whole-game glyph coverage, final mapping capacity, or final layout quality",
        },
    }
    if preserved_runtime_result:
        report["status"] = "runtime_pass"
        report["runtime_result"] = preserved_runtime_result
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-rom", type=Path, required=True)
    parser.add_argument("--font-png", type=Path, required=True)
    parser.add_argument("--output-rom", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--char", default="한")
    args = parser.parse_args()
    build(args.source_rom, args.font_png, args.output_rom, args.report, args.char)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
