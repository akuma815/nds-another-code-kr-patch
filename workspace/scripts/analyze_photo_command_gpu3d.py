#!/usr/bin/env python3
"""Trace and render the live GPU3D texture used by the photo command."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct

from PIL import Image


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def decode_bgr555(data: bytes, transparent_zero: bool) -> list[tuple[int, int, int, int]]:
    colors = []
    for index, offset in enumerate(range(0, len(data), 2)):
        value = struct.unpack_from("<H", data, offset)[0]
        colors.append(
            (
                (value & 0x1F) * 255 // 31,
                ((value >> 5) & 0x1F) * 255 // 31,
                ((value >> 10) & 0x1F) * 255 // 31,
                0 if transparent_zero and index == 0 else 255,
            )
        )
    return colors


def unpack_4bpp_linear(data: bytes, pixel_count: int) -> list[int]:
    if len(data) * 2 != pixel_count:
        raise ValueError("4bpp texture size does not match dimensions")
    result = []
    for value in data:
        result.extend((value & 0x0F, value >> 4))
    return result


def save_checker_preview(image: Image.Image, path: Path, scale: int = 4) -> None:
    enlarged = image.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)
    checker = Image.new("RGBA", enlarged.size)
    pixels = checker.load()
    assert pixels is not None
    for y in range(enlarged.height):
        for x in range(enlarged.width):
            shade = 184 if ((x // 16) + (y // 16)) % 2 else 224
            pixels[x, y] = (shade, shade, shade, 255)
    checker.alpha_composite(enlarged)
    checker.convert("RGB").save(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--guest-dir", type=Path, required=True)
    parser.add_argument("--decoded-resource", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(f"refusing nonempty output directory: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    live_report = json.loads(
        (args.guest_dir / "dump_report.json").read_text(encoding="utf-8")
    )
    candidates = [
        row
        for row in live_report["gpu3d_render_state"]["render_polygons"]
        if row["screen_bounds"] == [120, 38, 248, 70]
    ]
    if len(candidates) != 1:
        raise ValueError(f"expected one target polygon, got {len(candidates)}")
    polygon = candidates[0]
    if polygon["texture_format"] != 3 or (
        polygon["texture_width"], polygon["texture_height"]
    ) != (128, 32):
        raise ValueError("target polygon is not the expected 128x32 4bpp texture")

    tex_param = int(polygon["tex_param"], 16)
    tex_palette = int(polygon["tex_palette"], 16)
    texture_address = int(polygon["texture_address"], 16)
    texture_size = polygon["texture_width"] * polygon["texture_height"] // 2
    if not 0x20000 <= texture_address < 0x40000:
        raise ValueError("target texture is not in the live texture slot backed by bank B")
    bank_b = (args.guest_dir / "vram_B_20000.bin").read_bytes()
    bank_e = (args.guest_dir / "vram_E_10000.bin").read_bytes()
    texture_offset = texture_address - 0x20000
    texture = bank_b[texture_offset : texture_offset + texture_size]
    palette_offset = tex_palette * 16
    palette = bank_e[palette_offset : palette_offset + 32]

    resource = args.decoded_resource.read_bytes()
    flags, width, height, pixel_size, palette_size = struct.unpack_from(
        "<IHHII", resource, 0
    )
    if (width, height, pixel_size, palette_size) != (128, 32, 0x800, 0x20):
        raise ValueError("decoded source is not the expected pda/p02 envelope")
    if not flags & 0x00100000:
        raise ValueError("pda/p02 does not use its expected palette-first layout flag")
    source_palette = resource[0x10:0x30]
    source_texture = resource[0x30:]
    if source_palette != palette or source_texture != texture:
        raise ValueError("live VRAM texture/palette does not exactly match pda/p02")

    main_ram = (args.guest_dir / "arm9_main_ram_02000000_4m.bin").read_bytes()
    ram_offsets = []
    start = 0
    while True:
        offset = main_ram.find(texture, start)
        if offset < 0:
            break
        ram_offsets.append(offset)
        start = offset + 1
    if len(ram_offsets) != 1:
        raise ValueError(f"expected one live RAM texture copy, got {len(ram_offsets)}")

    indices = unpack_4bpp_linear(texture, width * height)
    colors = decode_bgr555(palette, transparent_zero=bool((tex_param >> 29) & 1))
    image = Image.new("RGBA", (width, height))
    image.putdata([colors[index] for index in indices])
    source_png = args.output_dir / "pda_p02_runtime_source.png"
    preview_png = args.output_dir / "pda_p02_runtime_source_x4_checker.png"
    image.save(source_png)
    save_checker_preview(image, preview_png)

    report = {
        "schema_version": 1,
        "status": "live_gpu3d_texture_traced_exactly_to_pda_p02",
        "target_label": {"source": "上に表示", "candidate_korean": "위에 표시"},
        "render_polygon": polygon,
        "live_texture": {
            "bank": "B",
            "bank_offset": f"0x{texture_offset:05X}",
            "size": len(texture),
            "sha256": sha256(texture),
            "palette_bank": "E",
            "palette_offset": f"0x{palette_offset:04X}",
            "palette_sha256": sha256(palette),
        },
        "main_ram_copy": {
            "guest_address": f"0x{0x02000000 + ram_offsets[0]:08X}",
            "exact_texture_matches": 1,
        },
        "source_resource": {
            "resource_id": "pda/p02",
            "decoded_path": str(args.decoded_resource),
            "decoded_sha256": sha256(resource),
            "layout": "16-byte header, 32-byte BGR555 palette, 2048-byte linear 4bpp texture",
            "texture_offset": "0x30",
            "palette_exact_match": True,
            "texture_exact_match": True,
        },
        "outputs": {
            "source_png": source_png.name,
            "source_png_sha256": sha256(source_png.read_bytes()),
            "preview_png": preview_png.name,
            "preview_png_sha256": sha256(preview_png.read_bytes()),
        },
        "safety": {
            "rom_built": False,
            "resource_modified": False,
            "serch_icon_resources_modified": False,
        },
    }
    report_path = args.output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
