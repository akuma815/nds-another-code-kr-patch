#!/usr/bin/env python3
"""Create a reproducible structural inventory of a Nintendo DS ROM.

The parser intentionally stays read-only. It records header, FAT, FNT and
overlay identities, and can extract immutable analysis copies without trying
to rebuild or reinterpret game-specific containers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def ascii_field(data: bytes, offset: int, size: int) -> str:
    return data[offset : offset + size].split(b"\0", 1)[0].decode("ascii", "replace")


def magic_hint(data: bytes) -> str:
    prefix = data[:8]
    printable = bytes(value for value in prefix if 0x20 <= value <= 0x7E)
    if len(printable) >= 3:
        return prefix.decode("ascii", "replace").rstrip("\0")
    return prefix.hex().upper()


class NdsRom:
    def __init__(self, path: Path):
        self.path = path
        self.data = path.read_bytes()
        self._initialize()

    @classmethod
    def from_bytes(cls, data: bytes, path_label: str = "<memory>") -> "NdsRom":
        """Parse a prospective ROM image without writing a temporary file."""
        result = cls.__new__(cls)
        result.path = Path(path_label)
        result.data = data
        result._initialize()
        return result

    def _initialize(self) -> None:
        if len(self.data) < 0x200:
            raise ValueError(f"ROM is too small for an NDS header: {self.path}")
        self.header = self._parse_header()
        self.fat = self._parse_fat()
        self.paths = self._parse_fnt()
        self.arm9_overlays = self._parse_overlay_table("arm9")
        self.arm7_overlays = self._parse_overlay_table("arm7")

    def _range(self, offset: int, size: int, label: str) -> bytes:
        end = offset + size
        if offset < 0 or size < 0 or end > len(self.data):
            raise ValueError(
                f"{label} range 0x{offset:X}..0x{end:X} exceeds ROM size 0x{len(self.data):X}"
            )
        return self.data[offset:end]

    def _parse_header(self) -> dict[str, Any]:
        data = self.data
        return {
            "title": ascii_field(data, 0x00, 12),
            "game_code": ascii_field(data, 0x0C, 4),
            "maker_code": ascii_field(data, 0x10, 2),
            "unit_code": data[0x12],
            "device_capacity": data[0x14],
            "rom_version": data[0x1E],
            "arm9": {
                "rom_offset": u32(data, 0x20),
                "entry_address": u32(data, 0x24),
                "load_address": u32(data, 0x28),
                "size": u32(data, 0x2C),
            },
            "arm7": {
                "rom_offset": u32(data, 0x30),
                "entry_address": u32(data, 0x34),
                "load_address": u32(data, 0x38),
                "size": u32(data, 0x3C),
            },
            "fnt": {"rom_offset": u32(data, 0x40), "size": u32(data, 0x44)},
            "fat": {"rom_offset": u32(data, 0x48), "size": u32(data, 0x4C)},
            "arm9_overlay_table": {
                "rom_offset": u32(data, 0x50),
                "size": u32(data, 0x54),
            },
            "arm7_overlay_table": {
                "rom_offset": u32(data, 0x58),
                "size": u32(data, 0x5C),
            },
            "banner_offset": u32(data, 0x68),
            "rom_used_size": u32(data, 0x80),
            "header_size": u32(data, 0x84),
            "header_crc16": u16(data, 0x15E),
        }

    def _parse_fat(self) -> list[tuple[int, int]]:
        info = self.header["fat"]
        raw = self._range(info["rom_offset"], info["size"], "FAT")
        if len(raw) % 8:
            raise ValueError(f"FAT size is not divisible by 8: {len(raw)}")
        entries = []
        for index in range(0, len(raw), 8):
            start, end = struct.unpack_from("<II", raw, index)
            if start > end or end > len(self.data):
                raise ValueError(f"Invalid FAT entry {index // 8}: 0x{start:X}..0x{end:X}")
            entries.append((start, end))
        return entries

    def _parse_fnt(self) -> dict[int, str]:
        info = self.header["fnt"]
        raw = self._range(info["rom_offset"], info["size"], "FNT")
        if len(raw) < 8:
            raise ValueError("FNT is too small")
        directory_count = u16(raw, 6)
        if not 1 <= directory_count <= 0x1000 or directory_count * 8 > len(raw):
            raise ValueError(f"Invalid FNT directory count: {directory_count}")

        directories: dict[int, tuple[int, int, int]] = {}
        for index in range(directory_count):
            offset = index * 8
            directories[0xF000 + index] = (
                u32(raw, offset),
                u16(raw, offset + 4),
                u16(raw, offset + 6),
            )

        paths: dict[int, str] = {}
        active: set[int] = set()

        def visit(directory_id: int, parent: PurePosixPath) -> None:
            if directory_id in active:
                raise ValueError(f"FNT directory cycle at 0x{directory_id:04X}")
            if directory_id not in directories:
                raise ValueError(f"Unknown FNT directory id 0x{directory_id:04X}")
            active.add(directory_id)
            cursor, file_id, _parent_id = directories[directory_id]
            while True:
                if cursor >= len(raw):
                    raise ValueError(f"FNT subtable for 0x{directory_id:04X} is truncated")
                type_length = raw[cursor]
                cursor += 1
                if type_length == 0:
                    break
                name_length = type_length & 0x7F
                if name_length == 0 or cursor + name_length > len(raw):
                    raise ValueError(f"Invalid FNT name in directory 0x{directory_id:04X}")
                name = raw[cursor : cursor + name_length].decode("ascii", "replace")
                cursor += name_length
                if type_length & 0x80:
                    if cursor + 2 > len(raw):
                        raise ValueError("Truncated FNT child directory id")
                    child_id = u16(raw, cursor)
                    cursor += 2
                    visit(child_id, parent / name)
                else:
                    if file_id >= len(self.fat):
                        raise ValueError(f"FNT file id {file_id} is outside FAT")
                    if file_id in paths:
                        raise ValueError(f"Duplicate FNT file id {file_id}")
                    paths[file_id] = (parent / name).as_posix()
                    file_id += 1
            active.remove(directory_id)

        visit(0xF000, PurePosixPath())
        return paths

    def _parse_overlay_table(self, cpu: str) -> list[dict[str, int]]:
        info = self.header[f"{cpu}_overlay_table"]
        if info["size"] == 0:
            return []
        raw = self._range(info["rom_offset"], info["size"], f"{cpu.upper()} overlay table")
        if len(raw) % 32:
            raise ValueError(f"{cpu.upper()} overlay table size is not divisible by 32")
        records = []
        for offset in range(0, len(raw), 32):
            values = struct.unpack_from("<8I", raw, offset)
            file_id = values[6]
            if file_id >= len(self.fat):
                raise ValueError(f"{cpu.upper()} overlay file id {file_id} is outside FAT")
            flags = values[7]
            records.append(
                {
                    "overlay_id": values[0],
                    "ram_address": values[1],
                    "ram_size": values[2],
                    "bss_size": values[3],
                    "static_init_start": values[4],
                    "static_init_end": values[5],
                    "file_id": file_id,
                    "compressed_size": flags & 0x00FFFFFF,
                    "flags": flags >> 24,
                }
            )
        return records

    def component_bytes(self, name: str) -> bytes:
        if name in ("arm9", "arm7"):
            info = self.header[name]
            return self._range(info["rom_offset"], info["size"], name.upper())
        if name == "banner":
            offset = self.header["banner_offset"]
            if offset == 0:
                return b""
            version = u16(self.data, offset)
            sizes = {1: 0x840, 2: 0x940, 3: 0xA40, 0x103: 0x23C0}
            size = sizes.get(version, 0x840)
            return self._range(offset, size, "banner")
        raise KeyError(name)

    def inventory(self) -> dict[str, Any]:
        overlay_ids = {
            row["file_id"] for row in self.arm9_overlays + self.arm7_overlays
        }
        files = []
        for file_id, (start, end) in enumerate(self.fat):
            payload = self.data[start:end]
            files.append(
                {
                    "file_id": file_id,
                    "path": self.paths.get(file_id),
                    "role": "overlay" if file_id in overlay_ids else "nitrofs_or_unnamed",
                    "rom_start": start,
                    "rom_end": end,
                    "size": end - start,
                    "sha256": sha256(payload),
                    "magic_hint": magic_hint(payload),
                }
            )
        suffixes = Counter(
            PurePosixPath(path).suffix.lower() or "<none>" for path in self.paths.values()
        )
        return {
            "schema_version": 1,
            "source": {
                "path": str(self.path),
                "size": len(self.data),
                "sha256": sha256(self.data),
            },
            "header": self.header,
            "components": {
                name: {"size": len(payload), "sha256": sha256(payload)}
                for name in ("arm9", "arm7", "banner")
                if (payload := self.component_bytes(name))
            },
            "counts": {
                "fat_entries": len(self.fat),
                "named_fnt_files": len(self.paths),
                "arm9_overlays": len(self.arm9_overlays),
                "arm7_overlays": len(self.arm7_overlays),
            },
            "extension_counts": dict(sorted(suffixes.items())),
            "arm9_overlays": self.arm9_overlays,
            "arm7_overlays": self.arm7_overlays,
            "files": files,
        }

    def extract(self, output_root: Path) -> None:
        output_root.mkdir(parents=True, exist_ok=True)
        (output_root / "arm9").mkdir(exist_ok=True)
        (output_root / "arm7").mkdir(exist_ok=True)
        (output_root / "overlays").mkdir(exist_ok=True)
        (output_root / "nitrofs").mkdir(exist_ok=True)
        (output_root / "banner").mkdir(exist_ok=True)
        (output_root / "arm9" / "arm9.bin").write_bytes(self.component_bytes("arm9"))
        (output_root / "arm7" / "arm7.bin").write_bytes(self.component_bytes("arm7"))
        banner = self.component_bytes("banner")
        if banner:
            (output_root / "banner" / "banner.bin").write_bytes(banner)
        for cpu, rows in (("arm9", self.arm9_overlays), ("arm7", self.arm7_overlays)):
            for row in rows:
                start, end = self.fat[row["file_id"]]
                filename = f"{cpu}_overlay_{row['overlay_id']:03d}_file_{row['file_id']:03d}.bin"
                (output_root / "overlays" / filename).write_bytes(self.data[start:end])
        for file_id, relative in self.paths.items():
            pure = PurePosixPath(relative)
            if pure.is_absolute() or ".." in pure.parts:
                raise ValueError(f"Unsafe NitroFS path: {relative}")
            target = output_root / "nitrofs" / Path(*pure.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            start, end = self.fat[file_id]
            target.write_bytes(self.data[start:end])


def diff_intervals(left: bytes, right: bytes) -> tuple[int, list[dict[str, int]]]:
    intervals = []
    start = None
    changed = 0
    common = min(len(left), len(right))
    for index in range(common):
        different = left[index] != right[index]
        if different:
            changed += 1
            if start is None:
                start = index
        elif start is not None:
            intervals.append({"start": start, "end": index, "size": index - start})
            start = None
    if start is not None:
        intervals.append({"start": start, "end": common, "size": common - start})
    if len(left) != len(right):
        intervals.append(
            {"start": common, "end": max(len(left), len(right)), "size": abs(len(left) - len(right))}
        )
        changed += abs(len(left) - len(right))
    return changed, intervals


def compare_roms(base: NdsRom, candidate: NdsRom) -> dict[str, Any]:
    changed_bytes, intervals = diff_intervals(base.data, candidate.data)
    component_rows = []
    for name in ("arm9", "arm7", "banner"):
        left = base.component_bytes(name)
        right = candidate.component_bytes(name)
        component_rows.append(
            {
                "component": name,
                "base_size": len(left),
                "candidate_size": len(right),
                "base_sha256": sha256(left),
                "candidate_sha256": sha256(right),
                "changed": left != right,
                "changed_bytes": diff_intervals(left, right)[0],
            }
        )
    file_rows = []
    max_files = max(len(base.fat), len(candidate.fat))
    for file_id in range(max_files):
        left = b""
        right = b""
        if file_id < len(base.fat):
            start, end = base.fat[file_id]
            left = base.data[start:end]
        if file_id < len(candidate.fat):
            start, end = candidate.fat[file_id]
            right = candidate.data[start:end]
        if left != right or base.paths.get(file_id) != candidate.paths.get(file_id):
            file_rows.append(
                {
                    "file_id": file_id,
                    "base_path": base.paths.get(file_id),
                    "candidate_path": candidate.paths.get(file_id),
                    "base_size": len(left),
                    "candidate_size": len(right),
                    "base_sha256": sha256(left),
                    "candidate_sha256": sha256(right),
                    "changed_bytes": diff_intervals(left, right)[0],
                }
            )
    return {
        "schema_version": 1,
        "base": {"path": str(base.path), "size": len(base.data), "sha256": sha256(base.data)},
        "candidate": {
            "path": str(candidate.path),
            "size": len(candidate.data),
            "sha256": sha256(candidate.data),
        },
        "whole_rom": {
            "changed_bytes": changed_bytes,
            "interval_count": len(intervals),
            "first_changed_offset": intervals[0]["start"] if intervals else None,
            "last_changed_end": intervals[-1]["end"] if intervals else None,
            "intervals": intervals,
        },
        "components": component_rows,
        "changed_files": file_rows,
        "header_equal": base.data[:0x200] == candidate.data[:0x200],
        "fat_equal": base.fat == candidate.fat,
        "fnt_paths_equal": base.paths == candidate.paths,
        "overlay_tables_equal": (
            base.arm9_overlays == candidate.arm9_overlays
            and base.arm7_overlays == candidate.arm7_overlays
        ),
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory")
    inventory.add_argument("--rom", type=Path, required=True)
    inventory.add_argument("--report", type=Path, required=True)
    inventory.add_argument("--extract-root", type=Path)
    compare = commands.add_parser("compare")
    compare.add_argument("--base", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "inventory":
        rom = NdsRom(args.rom)
        write_json(args.report, rom.inventory())
        if args.extract_root:
            rom.extract(args.extract_root)
    else:
        write_json(args.report, compare_roms(NdsRom(args.base), NdsRom(args.candidate)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
