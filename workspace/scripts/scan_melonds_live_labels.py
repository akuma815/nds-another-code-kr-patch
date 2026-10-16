#!/usr/bin/env python3
"""Read-only scan of a live melonDS process for resident UI-label forms.

The report stores only match metadata and small context windows. It does not
write to the process and deliberately avoids creating a multi-gigabyte dump.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import struct
import time

from analyze_game_font import GLYPH_SIZE, sjis_to_index


PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
MAX_MATCHES_PER_PATTERN = 128


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


def unpack_16x16_2bpp(data: bytes) -> list[list[int]]:
    if len(data) != GLYPH_SIZE:
        raise ValueError("glyph must be exactly 64 bytes")
    pixels = [[0] * 16 for _ in range(16)]
    pos = 0
    for tile_y, tile_x in ((0, 0), (0, 8), (8, 0), (8, 8)):
        for y in range(8):
            for group in range(2):
                value = data[pos]
                pos += 1
                for pixel in range(4):
                    pixels[tile_y + y][tile_x + group * 4 + pixel] = (
                        value >> (6 - pixel * 2)
                    ) & 3
    return pixels


def pixels_in_tile_order(pixels: list[list[int]]) -> list[int]:
    result: list[int] = []
    for tile_y, tile_x in ((0, 0), (0, 8), (8, 0), (8, 8)):
        for y in range(8):
            result.extend(pixels[tile_y + y][tile_x : tile_x + 8])
    return result


def pack_4bpp_tiled(pixels: list[list[int]], high_first: bool) -> bytes:
    values = pixels_in_tile_order(pixels)
    output = bytearray()
    for i in range(0, len(values), 2):
        a, b = values[i], values[i + 1]
        output.append((a << 4 | b) if high_first else (a | b << 4))
    return bytes(output)


def make_patterns(
    font_path: Path, arm9_path: Path | None, main_ram_host_base: int | None
) -> dict[str, bytes]:
    # Explicit CP932 bytes avoid console/code-page ambiguity in automation.
    sjis_codes = (0x8FE3, 0x82C9, 0x955C, 0x8EA6)  # 上に表示
    cp932 = bytes.fromhex("8F E3 82 C9 95 5C 8E A6")
    indices = [sjis_to_index(code) for code in sjis_codes]
    font = font_path.read_bytes()
    glyphs = [
        font[index * GLYPH_SIZE : (index + 1) * GLYPH_SIZE]
        for index in indices
    ]
    if any(len(glyph) != GLYPH_SIZE for glyph in glyphs):
        raise ValueError("font_lc.bin does not contain every target glyph")
    pixel_glyphs = [unpack_16x16_2bpp(glyph) for glyph in glyphs]
    patterns = {
        "cp932_concat": cp932,
        "cp932_nul_interleaved": b"".join(bytes((b, 0)) for b in cp932),
        "sjis_u16_le": b"".join(struct.pack("<H", code) for code in sjis_codes),
        "sjis_u32_le": b"".join(struct.pack("<I", code) for code in sjis_codes),
        "font_index_u16_le": b"".join(struct.pack("<H", index) for index in indices),
        "font_index_u32_le": b"".join(struct.pack("<I", index) for index in indices),
        "glyph_2bpp_concat": b"".join(glyphs),
        "glyph_4bpp_tiled_lo_concat": b"".join(
            pack_4bpp_tiled(pixels, high_first=False) for pixels in pixel_glyphs
        ),
        "glyph_4bpp_tiled_hi_concat": b"".join(
            pack_4bpp_tiled(pixels, high_first=True) for pixels in pixel_glyphs
        ),
        "glyph_8bpp_tiled_concat": b"".join(
            bytes(pixels_in_tile_order(pixels)) for pixels in pixel_glyphs
        ),
    }
    for ordinal, (glyph, pixels) in enumerate(zip(glyphs, pixel_glyphs, strict=True)):
        patterns[f"glyph{ordinal}_2bpp"] = glyph
        patterns[f"glyph{ordinal}_4bpp_tiled_lo"] = pack_4bpp_tiled(
            pixels, high_first=False
        )
        patterns[f"glyph{ordinal}_8bpp_tiled"] = bytes(pixels_in_tile_order(pixels))
    # Known resident-file anchors help distinguish the guest RAM allocation from
    # unrelated host buffers. Avoid headers because loaders commonly rewrite them.
    patterns["font_lc_anchor_1024"] = font[0x4000:0x4400]
    if arm9_path is not None:
        arm9 = arm9_path.read_bytes()
        patterns["arm9_anchor_1024"] = arm9[0x4000:0x4400]
    if main_ram_host_base is not None:
        patterns["main_ram_host_pointer"] = struct.pack("<Q", main_ram_host_base)
    return patterns


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--arm9", type=Path)
    parser.add_argument("--main-ram-host-base", type=lambda value: int(value, 0))
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--context-dir", type=Path, required=True)
    parser.add_argument("--chunk-size", type=lambda value: int(value, 0), default=0x400000)
    args = parser.parse_args()

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.VirtualQueryEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.POINTER(MEMORY_BASIC_INFORMATION),
        ctypes.c_size_t,
    ]
    kernel32.VirtualQueryEx.restype = ctypes.c_size_t
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

    patterns = make_patterns(args.font, args.arm9, args.main_ram_host_base)
    matches: dict[str, list[dict[str, int | str]]] = {name: [] for name in patterns}
    args.context_dir.mkdir(parents=True, exist_ok=True)
    readable_bytes = 0
    readable_regions = 0
    read_failures = 0
    started = time.time()
    address = 0
    max_address = 0x7FFFFFFFFFFF
    overlap = max(len(pattern) for pattern in patterns.values()) - 1

    try:
        while address < max_address:
            mbi = MEMORY_BASIC_INFORMATION()
            queried = kernel32.VirtualQueryEx(
                handle, ctypes.c_void_p(address), ctypes.byref(mbi), ctypes.sizeof(mbi)
            )
            if not queried:
                break
            base = int(mbi.BaseAddress or 0)
            size = int(mbi.RegionSize)
            next_address = base + size
            if next_address <= address:
                break
            readable = (
                mbi.State == MEM_COMMIT
                and not (mbi.Protect & PAGE_GUARD)
                and not (mbi.Protect & PAGE_NOACCESS)
            )
            if readable:
                readable_regions += 1
                offset = 0
                tail = b""
                while offset < size:
                    request = min(args.chunk_size, size - offset)
                    buffer = ctypes.create_string_buffer(request)
                    count = ctypes.c_size_t()
                    ok = kernel32.ReadProcessMemory(
                        handle,
                        ctypes.c_void_p(base + offset),
                        buffer,
                        request,
                        ctypes.byref(count),
                    )
                    if not ok and count.value == 0:
                        read_failures += 1
                        break
                    block = buffer.raw[: count.value]
                    readable_bytes += len(block)
                    haystack = tail + block
                    haystack_base = base + offset - len(tail)
                    for name, pattern in patterns.items():
                        if len(matches[name]) >= MAX_MATCHES_PER_PATTERN:
                            continue
                        start = 0
                        while len(matches[name]) < MAX_MATCHES_PER_PATTERN:
                            found = haystack.find(pattern, start)
                            if found < 0:
                                break
                            absolute = haystack_base + found
                            context_start = max(0, found - 0x800)
                            context_end = min(len(haystack), found + len(pattern) + 0x800)
                            context = haystack[context_start:context_end]
                            ordinal = len(matches[name])
                            context_name = f"{name}_{ordinal:03d}_{absolute:016X}.bin"
                            context_path = args.context_dir / context_name
                            context_path.write_bytes(context)
                            matches[name].append(
                                {
                                    "address": f"0x{absolute:016X}",
                                    "region_base": f"0x{base:016X}",
                                    "region_size": size,
                                    "protect": f"0x{mbi.Protect:08X}",
                                    "context_file": context_name,
                                    "context_sha256": sha256(context),
                                }
                            )
                            start = found + 1
                    tail = haystack[-overlap:] if overlap else b""
                    offset += len(block)
                    if len(block) < request:
                        break
            address = next_address
    finally:
        kernel32.CloseHandle(handle)

    report = {
        "schema_version": 1,
        "pid": args.pid,
        "mode": "read_only_live_process_scan",
        "target": "U+4E0A U+306B U+8868 U+793A",
        "target_cp932_hex": "8FE382C9955C8EA6",
        "font_path": str(args.font),
        "font_sha256": sha256(args.font.read_bytes()),
        "pattern_lengths": {name: len(value) for name, value in patterns.items()},
        "readable_regions": readable_regions,
        "readable_bytes": readable_bytes,
        "read_failures": read_failures,
        "elapsed_seconds": round(time.time() - started, 3),
        "match_counts": {name: len(rows) for name, rows in matches.items()},
        "matches": matches,
        "interpretation_boundary": (
            "A hit proves only that the tested representation was resident in the host "
            "process. A miss does not exclude transformed, composited, or GPU-owned data."
        ),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("pid", "readable_regions", "readable_bytes", "read_failures", "elapsed_seconds", "match_counts")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
