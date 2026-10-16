#!/usr/bin/env python3
"""Dump confirmed guest main RAM and physical VRAM banks from melonDS 1.1.

This is a read-only diagnostic. The VRAM bank locator uses the self-pointer
table in melonDS 1.1's GPU object and validates all nine documented bank
offsets before writing any dump.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import struct


PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
VRAM_BANKS = (
    ("A", 0x20000),
    ("B", 0x20000),
    ("C", 0x20000),
    ("D", 0x20000),
    ("E", 0x10000),
    ("F", 0x04000),
    ("G", 0x04000),
    ("H", 0x08000),
    ("I", 0x04000),
)
GPU2D_STATE_SIZE = 0xB0
GPU3D_STATE_SIZE = 0x1A6090
GPU3D_POLYGON_SIZE = 0xE0
GPU3D_VERTEX_SIZE = 0x40


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def parse_gpu2d_state(data: bytes, expected_num: int) -> dict[str, object]:
    """Parse the register-bearing prefix used by the current melonDS GPU2D class."""
    if len(data) != GPU2D_STATE_SIZE:
        raise ValueError(f"expected {GPU2D_STATE_SIZE:#x} GPU2D bytes, got {len(data):#x}")
    vtable, num = struct.unpack_from("<QI", data, 0)
    enabled = data[0x0C]
    if num != expected_num or enabled not in (0, 1):
        raise ValueError(
            f"GPU2D identity mismatch: expected num={expected_num}, got num={num}, enabled={enabled}"
        )
    if vtable == 0:
        raise ValueError("GPU2D vtable pointer is null")

    disp_cnt = struct.unpack_from("<I", data, 0x10)[0]
    disp_cnt_latch = list(struct.unpack_from("<3I", data, 0x14))
    bg_cnt = list(struct.unpack_from("<4H", data, 0x24))
    bg_x_pos = list(struct.unpack_from("<4H", data, 0x2C))
    bg_y_pos = list(struct.unpack_from("<4H", data, 0x34))
    return {
        "vtable_host_address": f"0x{vtable:016X}",
        "num": num,
        "enabled": bool(enabled),
        "disp_cnt": f"0x{disp_cnt:08X}",
        "disp_cnt_latch": [f"0x{value:08X}" for value in disp_cnt_latch],
        "layer_enable": data[0x20],
        "obj_enable": data[0x21],
        "forced_blank": data[0x22],
        "bg_cnt": [f"0x{value:04X}" for value in bg_cnt],
        "bg_x_pos": bg_x_pos,
        "bg_y_pos": bg_y_pos,
    }


def parse_gpu3d_render_state(data: bytes, host_address: int) -> dict[str, object]:
    """Parse the active render list from the current melonDS GPU3D class."""
    if len(data) != GPU3D_STATE_SIZE:
        raise ValueError(f"expected {GPU3D_STATE_SIZE:#x} GPU3D bytes, got {len(data):#x}")

    nds_pointer, gpu_pointer = struct.unpack_from("<2Q", data, 0)
    disp_cnt = struct.unpack_from("<I", data, 0x1D0C)[0]
    render_disp_cnt = struct.unpack_from("<I", data, 0x1D94)[0]
    render_count = struct.unpack_from("<I", data, 0x1A6080)[0]
    if not nds_pointer or not gpu_pointer:
        raise ValueError("GPU3D owner pointers are null")
    if render_count > 2048:
        raise ValueError(f"invalid GPU3D render polygon count: {render_count}")

    vertex_ram_offset = 0x205C
    vertex_ram_end = vertex_ram_offset + GPU3D_VERTEX_SIZE * (6144 * 2)
    polygon_ram_offset = 0xC2060
    polygon_ram_end = polygon_ram_offset + GPU3D_POLYGON_SIZE * (2048 * 2)
    render_pointers = struct.unpack_from(f"<{render_count}Q", data, 0x1A2080)
    polygons = []
    for render_index, pointer in enumerate(render_pointers):
        polygon_offset = pointer - host_address
        if not polygon_ram_offset <= polygon_offset < polygon_ram_end:
            raise ValueError(
                f"render polygon {render_index} pointer is outside PolygonRAM: {pointer:#x}"
            )
        if (polygon_offset - polygon_ram_offset) % GPU3D_POLYGON_SIZE:
            raise ValueError(f"render polygon {render_index} pointer is misaligned")
        num_vertices = struct.unpack_from("<I", data, polygon_offset + 0x50)[0]
        if not 1 <= num_vertices <= 10:
            raise ValueError(
                f"render polygon {render_index} has invalid vertex count {num_vertices}"
            )
        attr, tex_param, tex_palette = struct.unpack_from(
            "<3I", data, polygon_offset + 0xA8
        )
        polygon_type = struct.unpack_from("<i", data, polygon_offset + 0xBC)[0]
        _, _, y_top, y_bottom, x_top, x_bottom, sort_key = struct.unpack_from(
            "<2I4iI", data, polygon_offset + 0xC0
        )
        vertex_pointers = struct.unpack_from(
            f"<{num_vertices}Q", data, polygon_offset
        )
        vertices = []
        for vertex_pointer in vertex_pointers:
            vertex_offset = vertex_pointer - host_address
            if not vertex_ram_offset <= vertex_offset < vertex_ram_end:
                raise ValueError(
                    f"render polygon {render_index} vertex pointer is outside VertexRAM"
                )
            if (vertex_offset - vertex_ram_offset) % GPU3D_VERTEX_SIZE:
                raise ValueError(f"render polygon {render_index} vertex pointer is misaligned")
            tex_s, tex_t = struct.unpack_from("<2h", data, vertex_offset + 0x1C)
            final_x, final_y = struct.unpack_from("<2i", data, vertex_offset + 0x24)
            vertices.append(
                {
                    "host_address": f"0x{vertex_pointer:016X}",
                    "texture_coordinates": [tex_s, tex_t],
                    "screen_position": [final_x, final_y],
                }
            )

        texture_address = (tex_param & 0xFFFF) << 3
        polygons.append(
            {
                "render_index": render_index,
                "host_address": f"0x{pointer:016X}",
                "polygon_ram_index": (polygon_offset - polygon_ram_offset)
                // GPU3D_POLYGON_SIZE,
                "num_vertices": num_vertices,
                "attr": f"0x{attr:08X}",
                "tex_param": f"0x{tex_param:08X}",
                "tex_palette": f"0x{tex_palette:08X}",
                "texture_address": f"0x{texture_address:05X}",
                "texture_width": 8 << ((tex_param >> 20) & 7),
                "texture_height": 8 << ((tex_param >> 23) & 7),
                "texture_format": (tex_param >> 26) & 7,
                "polygon_type": polygon_type,
                "screen_bounds": [x_top, y_top, x_bottom, y_bottom],
                "sort_key": f"0x{sort_key:08X}",
                "vertices": vertices,
            }
        )

    return {
        "nds_host_address": f"0x{nds_pointer:016X}",
        "gpu_host_address": f"0x{gpu_pointer:016X}",
        "disp_cnt": f"0x{disp_cnt:08X}",
        "render_disp_cnt": f"0x{render_disp_cnt:08X}",
        "render_num_polygons": render_count,
        "render_polygons": polygons,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--main-ram-host-base", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--object-region-base", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--object-region-size", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, args.pid
    )
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    def read_exact(address: int, size: int) -> bytes:
        output = bytearray()
        while len(output) < size:
            request = min(0x400000, size - len(output))
            buffer = ctypes.create_string_buffer(request)
            count = ctypes.c_size_t()
            ok = kernel32.ReadProcessMemory(
                handle,
                ctypes.c_void_p(address + len(output)),
                buffer,
                request,
                ctypes.byref(count),
            )
            if not ok or count.value != request:
                raise ctypes.WinError(ctypes.get_last_error())
            output.extend(buffer.raw[: count.value])
        return bytes(output)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    try:
        # Retail DS main RAM is 4 MiB even though melonDS reserves a larger DSi-capable block.
        main_ram = read_exact(args.main_ram_host_base, 0x400000)
        object_blob = read_exact(args.object_region_base, args.object_region_size)

        pointer_table_candidates: list[dict[str, object]] = []
        cumulative_offsets = []
        running = 0
        for _, size in VRAM_BANKS:
            cumulative_offsets.append(running)
            running += size
        total_vram_size = running

        for offset in range(0, len(object_blob) - 8 * len(VRAM_BANKS) + 1, 8):
            table_address = args.object_region_base + offset
            first = struct.unpack_from("<Q", object_blob, offset)[0]
            if first + total_vram_size != table_address:
                continue
            pointers = list(struct.unpack_from("<9Q", object_blob, offset))
            expected = [first + delta for delta in cumulative_offsets]
            if pointers == expected:
                pointer_table_candidates.append(
                    {
                        "pointer_table_address": table_address,
                        "vram_a_address": first,
                        "pointers": pointers,
                    }
                )

        if len(pointer_table_candidates) != 1:
            raise RuntimeError(
                f"expected one validated VRAM pointer table, got {len(pointer_table_candidates)}"
            )
        candidate = pointer_table_candidates[0]
        pointers = candidate["pointers"]
        assert isinstance(pointers, list)

        table_address = int(candidate["pointer_table_address"])
        expected_masks = (
            0x1FFFF, 0x1FFFF, 0x1FFFF, 0x1FFFF,
            0x0FFFF, 0x03FFF, 0x03FFF, 0x07FFF, 0x03FFF,
        )
        mask_bytes = read_exact(table_address + 9 * 8, 9 * 4)
        actual_masks = struct.unpack("<9I", mask_bytes)
        if actual_masks != expected_masks:
            raise RuntimeError("VRAMMask validation failed after the pointer table")

        vram_a = int(pointers[0])
        # In tag 1.1 GPU.h, Palette and OAM immediately precede VRAM_A.
        palette = read_exact(vram_a - 0x1000, 0x800)
        oam = read_exact(vram_a - 0x800, 0x800)
        # MSVC may place the later private-member access block before the public
        # fields, so do not hardcode the VRAMCNT offset. On an active retail-DS
        # frame all nine banks in this title are enabled; find that unique run.
        gpu_prefix_address = vram_a - 0x2000
        gpu_prefix = read_exact(gpu_prefix_address, 0x1000)
        vramcnt_candidates = [
            offset
            for offset in range(len(gpu_prefix) - 9)
            if all(0x80 <= value < 0xA0 for value in gpu_prefix[offset : offset + 9])
            and gpu_prefix[offset + 9] < 4
        ]
        if len(vramcnt_candidates) != 1:
            raise RuntimeError(
                f"expected one enabled VRAMCNT run, got {len(vramcnt_candidates)}"
            )
        vramcnt_offset = vramcnt_candidates[0]
        vramcnt = gpu_prefix[vramcnt_offset : vramcnt_offset + 9]
        vramstat = gpu_prefix[vramcnt_offset + 9]
        vramcnt_host_address = gpu_prefix_address + vramcnt_offset

        map_address = table_address + 9 * 8 + 9 * 4
        map_blob = read_exact(map_address, 89 * 4)
        map_offset = 0
        def take_u32(count: int) -> list[int]:
            nonlocal map_offset
            values = list(struct.unpack_from(f"<{count}I", map_blob, map_offset))
            map_offset += count * 4
            return values

        vram_maps = {
            "LCDC": take_u32(1),
            "ABG": take_u32(0x20),
            "AOBJ": take_u32(0x10),
            "BBG": take_u32(0x8),
            "BOBJ": take_u32(0x8),
            "ABGExtPal": take_u32(4),
            "AOBJExtPal": take_u32(1),
            "BBGExtPal": take_u32(4),
            "BOBJExtPal": take_u32(1),
            "Texture": take_u32(4),
            "TexPal": take_u32(8),
            "ARM7": take_u32(2),
        }

        # Current melonDS master places GPU2D_A and GPU2D_B immediately after
        # the four flattened VRAM pointer arrays. Locate them from the already
        # validated VRAM table instead of from a process-specific absolute address.
        vram_ptr_count = 0x20 + 0x10 + 0x8 + 0x8
        gpu2d_a_address = map_address + len(map_blob) + vram_ptr_count * 8
        gpu2d_a_blob = read_exact(gpu2d_a_address, GPU2D_STATE_SIZE)
        gpu2d_b_address = gpu2d_a_address + GPU2D_STATE_SIZE
        gpu2d_b_blob = read_exact(gpu2d_b_address, GPU2D_STATE_SIZE)
        gpu2d_a = parse_gpu2d_state(gpu2d_a_blob, 0)
        gpu2d_b = parse_gpu2d_state(gpu2d_b_blob, 1)
        if gpu2d_a["vtable_host_address"] != gpu2d_b["vtable_host_address"]:
            raise RuntimeError("GPU2D A/B vtable pointers differ")
        gpu3d_address = gpu2d_b_address + GPU2D_STATE_SIZE
        gpu3d_blob = read_exact(gpu3d_address, GPU3D_STATE_SIZE)
        gpu3d = parse_gpu3d_render_state(gpu3d_blob, gpu3d_address)

        (args.out_dir / "arm9_main_ram_02000000_4m.bin").write_bytes(main_ram)
        (args.out_dir / "palette_05000000_2k.bin").write_bytes(palette)
        (args.out_dir / "oam_07000000_2k.bin").write_bytes(oam)
        (args.out_dir / "gpu2d_a_state.bin").write_bytes(gpu2d_a_blob)
        (args.out_dir / "gpu2d_b_state.bin").write_bytes(gpu2d_b_blob)
        (args.out_dir / "gpu3d_state.bin").write_bytes(gpu3d_blob)
        bank_rows = []
        all_vram = bytearray()
        for (name, size), address in zip(VRAM_BANKS, pointers, strict=True):
            data = read_exact(address, size)
            filename = f"vram_{name}_{size:05X}.bin"
            (args.out_dir / filename).write_bytes(data)
            bank_rows.append(
                {
                    "bank": name,
                    "host_address": f"0x{address:016X}",
                    "size": size,
                    "filename": filename,
                    "sha256": sha256(data),
                }
            )
            all_vram.extend(data)
        (args.out_dir / "vram_all_physical_banks.bin").write_bytes(all_vram)
    finally:
        kernel32.CloseHandle(handle)

    report = {
        "schema_version": 1,
        "pid": args.pid,
        "mode": "read_only_live_guest_memory_dump",
        "main_ram": {
            "guest_range": "0x02000000..0x023FFFFF",
            "host_base": f"0x{args.main_ram_host_base:016X}",
            "size": len(main_ram),
            "filename": "arm9_main_ram_02000000_4m.bin",
            "sha256": sha256(main_ram),
        },
        "melon_object_region": {
            "host_base": f"0x{args.object_region_base:016X}",
            "size": args.object_region_size,
        },
        "vram_pointer_table": {
            "host_address": f"0x{candidate['pointer_table_address']:016X}",
            "validated_total_physical_bytes": total_vram_size,
            "validation": "nine pointers exactly match melonDS 1.1 GPU.h bank offsets",
        },
        "gpu_mapping_state": {
            "vramcnt_host_address": f"0x{vramcnt_host_address:016X}",
            "vramcnt_hex": [f"{value:02X}" for value in vramcnt],
            "vramstat_hex": f"{vramstat:02X}",
            "maps": {
                name: [f"0x{value:08X}" for value in values]
                for name, values in vram_maps.items()
            },
            "palette": {
                "filename": "palette_05000000_2k.bin",
                "sha256": sha256(palette),
            },
            "oam": {
                "filename": "oam_07000000_2k.bin",
                "sha256": sha256(oam),
            },
        },
        "gpu2d_register_state": {
            "layout": "melonDS master GPU2D class, validated adjacent A/B instances",
            "engine_a": {
                "host_address": f"0x{gpu2d_a_address:016X}",
                "filename": "gpu2d_a_state.bin",
                "sha256": sha256(gpu2d_a_blob),
                **gpu2d_a,
            },
            "engine_b": {
                "host_address": f"0x{gpu2d_b_address:016X}",
                "filename": "gpu2d_b_state.bin",
                "sha256": sha256(gpu2d_b_blob),
                **gpu2d_b,
            },
        },
        "gpu3d_render_state": {
            "layout": "melonDS master GPU3D class, validated render polygon and vertex pointers",
            "host_address": f"0x{gpu3d_address:016X}",
            "filename": "gpu3d_state.bin",
            "sha256": sha256(gpu3d_blob),
            **gpu3d,
        },
        "vram_banks": bank_rows,
        "all_vram": {
            "filename": "vram_all_physical_banks.bin",
            "size": len(all_vram),
            "sha256": sha256(bytes(all_vram)),
        },
    }
    report_path = args.out_dir / "dump_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
