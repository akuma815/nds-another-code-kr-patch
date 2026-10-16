#!/usr/bin/env python3
"""Parse Another Code's pack directory and audit candidate nonzero glyph slots.

The game keeps archive names in ``head.inf`` and per-archive resource records
in slices of ``head.bin``.  Resource bodies use a 16-byte ``12 3D DA`` wrapper;
kind 00 is stored/uncompressed and kind 01 is compressed.  Raw two-byte code
hits in binary payloads are deliberately informational and never promoted to
text-reference evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


RECORD_SIZE = 0x20
NAME_SIZE = 0x10
WRAPPER_SIZE = 0x10
WRAPPER_PREFIX = b"\x12\x3D\xDA"
DIRECTIVE = re.compile(r"%([A-Za-z])(?:\(([^)]*)\))?")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


@dataclass(frozen=True)
class DirectoryRecord:
    name: str
    offset: int
    size: int
    record_offset: int


def decode_name(raw: bytes) -> str:
    value = raw.split(b"\0", 1)[0]
    if not value:
        raise ValueError("empty directory-record name")
    try:
        return value.decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError(f"non-ASCII directory-record name: {value.hex()}") from error


def parse_directory(data: bytes, *, base_offset: int = 0) -> tuple[list[DirectoryRecord], int]:
    """Parse 0x20-byte records through the mandatory END sentinel."""
    if len(data) % RECORD_SIZE:
        raise ValueError("directory byte length is not divisible by 0x20")
    rows: list[DirectoryRecord] = []
    for relative in range(0, len(data), RECORD_SIZE):
        record = data[relative : relative + RECORD_SIZE]
        name = decode_name(record[:NAME_SIZE])
        offset, size = struct.unpack_from("<II", record, 0x10)
        if name == "END":
            if offset != 0xFFFFFFFF or size != 0xFFFFFFFF:
                raise ValueError("END sentinel does not contain FFFFFFFF fields")
            return rows, relative + RECORD_SIZE
        if offset == 0xFFFFFFFF or size == 0xFFFFFFFF:
            raise ValueError(f"ordinary record {name!r} uses sentinel fields")
        rows.append(DirectoryRecord(name, offset, size, base_offset + relative))
    raise ValueError("directory has no END sentinel")


def validate_nonoverlap(rows: Iterable[DirectoryRecord], container_size: int) -> None:
    previous_end = 0
    for row in sorted(rows, key=lambda item: item.offset):
        if row.offset % RECORD_SIZE:
            raise ValueError(f"{row.name}: offset is not 0x20 aligned")
        end = row.offset + row.size
        if end > container_size:
            raise ValueError(f"{row.name}: range exceeds container")
        if row.offset < previous_end:
            raise ValueError(f"{row.name}: range overlaps previous record")
        previous_end = end


def classify_wrapper(data: bytes) -> dict[str, int | str | bool | None]:
    if len(data) < WRAPPER_SIZE or not data.startswith(WRAPPER_PREFIX):
        return {
            "kind": "raw_or_unknown",
            "wrapper_type": None,
            "declared_unpacked_size": None,
            "declared_payload_size": None,
            "payload_size_matches_entry": None,
        }
    wrapper_type = data[3]
    unpacked_size, payload_size, reserved = struct.unpack_from("<III", data, 4)
    if wrapper_type == 0:
        kind = "stored"
        size_matches: bool | None = unpacked_size == len(data) - WRAPPER_SIZE
    elif wrapper_type == 1:
        kind = "compressed"
        size_matches = payload_size == len(data) - WRAPPER_SIZE
    else:
        kind = "unknown_wrapper_type"
        size_matches = None
    return {
        "kind": kind,
        "wrapper_type": wrapper_type,
        "declared_unpacked_size": unpacked_size,
        "declared_payload_size": payload_size,
        "payload_size_matches_entry": size_matches,
        "reserved_u32": reserved,
    }


def decompress_type1(payload: bytes, expected_size: int) -> bytes:
    """Decode the ARM9 routine at 0x02026B9C (64 KiB LZSS ring)."""
    window = bytearray(0x10000)
    window_offset = 0xFEFD
    source_offset = 0
    flags = 0
    output = bytearray()
    while len(output) < expected_size:
        flags >>= 1
        if not flags & 0x100:
            if source_offset >= len(payload):
                raise ValueError("compressed stream ended before a flag byte")
            flags = payload[source_offset] | 0xFF00
            source_offset += 1
        if flags & 1:
            if source_offset >= len(payload):
                raise ValueError("compressed stream ended before a literal")
            value = payload[source_offset]
            source_offset += 1
            output.append(value)
            window[window_offset] = value
            window_offset = (window_offset + 1) & 0xFFFF
            continue
        if source_offset + 3 > len(payload):
            raise ValueError("compressed stream ended inside a back-reference")
        reference = payload[source_offset] | (payload[source_offset + 1] << 8)
        length = payload[source_offset + 2] + 4
        source_offset += 3
        for _ in range(length):
            value = window[reference]
            reference = (reference + 1) & 0xFFFF
            output.append(value)
            window[window_offset] = value
            window_offset = (window_offset + 1) & 0xFFFF
            if len(output) == expected_size:
                break
    if source_offset != len(payload):
        raise ValueError(
            f"compressed stream has {len(payload) - source_offset} trailing bytes"
        )
    return bytes(output)


def compress_type1(data: bytes) -> bytes:
    """Encode the ARM9 type-1 stream using only already-emitted window bytes."""
    if not data:
        return b""
    minimum_match = 4
    maximum_match = 0x103
    maximum_distance = 0x10000
    positions: dict[bytes, list[int]] = {}
    output = bytearray()
    cursor = 0

    def remember(position: int) -> None:
        if position + minimum_match > len(data):
            return
        key = data[position : position + minimum_match]
        bucket = positions.setdefault(key, [])
        bucket.append(position)
        cutoff = position - maximum_distance
        while bucket and bucket[0] < cutoff:
            del bucket[0]

    while cursor < len(data):
        flag_offset = len(output)
        output.append(0)
        flags = 0
        for bit in range(8):
            if cursor >= len(data):
                break
            match_position = -1
            match_length = 0
            if cursor + minimum_match <= len(data):
                key = data[cursor : cursor + minimum_match]
                candidates = positions.get(key, [])
                cutoff = cursor - maximum_distance
                while candidates and candidates[0] < cutoff:
                    del candidates[0]
                for candidate in reversed(candidates[-1024:]):
                    limit = min(maximum_match, len(data) - cursor)
                    length = minimum_match
                    while length < limit and data[candidate + length] == data[cursor + length]:
                        length += 1
                    if length > match_length:
                        match_position = candidate
                        match_length = length
                        if length == limit:
                            break
            start = cursor
            if match_length >= minimum_match:
                reference = (0xFEFD + match_position) & 0xFFFF
                output.extend(struct.pack("<HB", reference, match_length - minimum_match))
                cursor += match_length
            else:
                flags |= 1 << bit
                output.append(data[cursor])
                cursor += 1
            for position in range(start, cursor):
                remember(position)
        output[flag_offset] = flags
    return bytes(output)


def decoded_payload(data: bytes, wrapper: dict[str, int | str | bool | None]) -> bytes:
    kind = wrapper["kind"]
    if kind == "stored":
        return data[WRAPPER_SIZE:]
    if kind == "compressed":
        return decompress_type1(
            data[WRAPPER_SIZE:], int(wrapper["declared_unpacked_size"])
        )
    return data


def has_japanese(text: str) -> bool:
    return any(
        "\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff"
        for char in text
    )


def nul_cp932_candidates(data: bytes) -> list[tuple[int, bytes, str, str]]:
    """Return bounded reversible CP932 strings, keeping strong/weak separate."""
    result: list[tuple[int, bytes, str, str]] = []
    start = 0
    while start < len(data):
        end = data.find(b"\0", start)
        if end < 0:
            end = len(data)
        raw = data[start:end]
        if 2 <= len(raw) <= 0x10000:
            try:
                text = raw.decode("cp932")
            except UnicodeDecodeError:
                text = ""
            if text and text.encode("cp932") == raw and has_japanese(text):
                directives = [match.group(0) for match in DIRECTIVE.finditer(text)]
                confidence = (
                    "strong_terminal_script"
                    if text.endswith("%e") and directives
                    else "weak_nul_cp932"
                )
                result.append((start, raw, text, confidence))
        start = end + 1
    return result


def character_codes(text: str) -> set[str]:
    result: set[str] = set()
    for char in text:
        encoded = char.encode("cp932")
        if len(encoded) == 2:
            result.add(encoded.hex().upper())
    return result


def count_occurrences(data: bytes, needle: bytes) -> int:
    count = 0
    start = 0
    while True:
        offset = data.find(needle, start)
        if offset < 0:
            return count
        count += 1
        start = offset + 1


def loader_string_offsets(executable: bytes) -> dict[str, list[int]]:
    strings = (
        "data/pack/head.inf",
        "data/pack/head.bin",
        "data/pack/%s.bin",
        "data/font.bin",
        "data/font_lc.bin",
        "data/db_font.bin",
    )
    result: dict[str, list[int]] = {}
    for value in strings:
        needle = value.encode("ascii")
        offsets: list[int] = []
        start = 0
        while True:
            found = executable.find(needle, start)
            if found < 0:
                break
            offsets.append(found)
            start = found + 1
        result[value] = offsets
    return result


def audit(args: argparse.Namespace) -> dict:
    pack_dir = args.pack_dir
    head_inf = (pack_dir / "head.inf").read_bytes()
    head_bin = (pack_dir / "head.bin").read_bytes()
    archives, inf_consumed = parse_directory(head_inf)
    if inf_consumed != len(head_inf):
        raise ValueError("head.inf contains trailing data after END")
    validate_nonoverlap(archives, len(head_bin))

    mapping_bytes = args.mapping.read_bytes()
    mapping = json.loads(mapping_bytes.decode("utf-8"))
    selected = [row for row in mapping["mappings"] if row["original_glyph"] == "nonzero"]
    selected_codes = {row["code"]: bytes.fromhex(row["code"]) for row in selected}
    selected_indices = {row["code"]: int(row["glyph_index"]) for row in selected}

    font = args.font.read_bytes()
    alternate_font = args.alternate_font.read_bytes()
    if len(font) % 64:
        raise ValueError("font size is not divisible by 64")
    if len(alternate_font) != len(font):
        raise ValueError("alternate font size does not match primary font")
    glyph_count = len(font) // 64
    for code, index in selected_indices.items():
        if not 0 <= index < glyph_count:
            raise ValueError(f"mapping {code} has an out-of-range glyph index")
    glyph_payloads = {
        code: font[index * 64 : (index + 1) * 64]
        for code, index in selected_indices.items()
    }
    all_mappings = mapping["mappings"]
    alternate_states = Counter(
        "all_zero"
        if not any(alternate_font[int(row["glyph_index"]) * 64 : (int(row["glyph_index"]) + 1) * 64])
        else "nonzero"
        for row in all_mappings
    )
    equal_selected_glyphs = sum(
        font[int(row["glyph_index"]) * 64 : (int(row["glyph_index"]) + 1) * 64]
        == alternate_font[int(row["glyph_index"]) * 64 : (int(row["glyph_index"]) + 1) * 64]
        for row in all_mappings
    )

    archive_rows: list[dict] = []
    resource_count = 0
    wrapper_counts: Counter[str] = Counter()
    raw_code_hits: Counter[str] = Counter()
    raw_code_entry_hits: Counter[str] = Counter()
    exact_glyph_hits: Counter[str] = Counter()
    exact_glyph_locations: list[dict[str, int | str]] = []
    resources_with_raw_hits = 0
    decoded_bytes = 0
    decoded_sha256_rows: list[dict[str, int | str]] = []
    strong_text_candidates: list[dict[str, int | str]] = []
    weak_text_candidate_count = 0
    selected_text_code_hits: dict[str, list[dict[str, int | str]]] = {
        code: [] for code in selected_codes
    }

    for archive in archives:
        archive_path = pack_dir / f"{archive.name}.bin"
        if not archive_path.is_file():
            raise ValueError(f"missing archive file: {archive_path}")
        archive_data = archive_path.read_bytes()
        descriptor_data = head_bin[archive.offset : archive.offset + archive.size]
        resources, consumed = parse_directory(descriptor_data, base_offset=archive.offset)
        if consumed != len(descriptor_data):
            raise ValueError(f"{archive.name}: descriptor slice has trailing bytes")
        validate_nonoverlap(resources, len(archive_data))
        archive_wrapper_counts: Counter[str] = Counter()
        archive_raw_hit_resources = 0
        archive_exact_glyph_hits = 0
        for resource in resources:
            resource_count += 1
            body = archive_data[resource.offset : resource.offset + resource.size]
            wrapper = classify_wrapper(body)
            kind = str(wrapper["kind"])
            wrapper_counts[kind] += 1
            archive_wrapper_counts[kind] += 1
            if wrapper.get("reserved_u32") not in (None, 0):
                raise ValueError(f"{archive.name}/{resource.name}: nonzero wrapper reserved field")
            if kind in ("stored", "compressed") and not wrapper["payload_size_matches_entry"]:
                raise ValueError(f"{archive.name}/{resource.name}: wrapper size mismatch")

            decoded = decoded_payload(body, wrapper)
            decoded_bytes += len(decoded)
            if kind == "compressed" and len(decoded) != wrapper["declared_unpacked_size"]:
                raise ValueError(f"{archive.name}/{resource.name}: decoded size mismatch")
            if kind == "compressed":
                decoded_sha256_rows.append(
                    {
                        "archive": archive.name,
                        "resource": resource.name,
                        "decoded_size": len(decoded),
                        "decoded_sha256": sha256(decoded),
                    }
                )
            for offset, candidate_raw, text, confidence in nul_cp932_candidates(decoded):
                if confidence == "strong_terminal_script":
                    strong_text_candidates.append(
                        {
                            "archive": archive.name,
                            "resource": resource.name,
                            "decoded_offset": offset,
                            "length": len(candidate_raw),
                            "text": text,
                        }
                    )
                else:
                    weak_text_candidate_count += 1
                for code in character_codes(text) & set(selected_codes):
                    selected_text_code_hits[code].append(
                        {
                            "archive": archive.name,
                            "resource": resource.name,
                            "decoded_offset": offset,
                            "confidence": confidence,
                        }
                    )

            hit_here = False
            for code, needle in selected_codes.items():
                count = count_occurrences(body, needle)
                if count:
                    hit_here = True
                    raw_code_hits[code] += count
                    raw_code_entry_hits[code] += 1
            if hit_here:
                resources_with_raw_hits += 1
                archive_raw_hit_resources += 1

            for code, glyph in glyph_payloads.items():
                start = 0
                while True:
                    relative = body.find(glyph, start)
                    if relative < 0:
                        break
                    exact_glyph_hits[code] += 1
                    archive_exact_glyph_hits += 1
                    exact_glyph_locations.append(
                        {
                            "code": code,
                            "glyph_index": selected_indices[code],
                            "archive": archive.name,
                            "resource": resource.name,
                            "resource_relative_offset": relative,
                            "archive_offset": resource.offset + relative,
                            "wrapper_kind": kind,
                        }
                    )
                    start = relative + 1

        archive_rows.append(
            {
                "name": archive.name,
                "head_bin_offset": archive.offset,
                "descriptor_size": archive.size,
                "resource_count": len(resources),
                "archive_size": len(archive_data),
                "archive_sha256": sha256(archive_data),
                "wrapper_counts": dict(sorted(archive_wrapper_counts.items())),
                "resources_with_raw_selected_code_hits": archive_raw_hit_resources,
                "exact_selected_glyph_payload_hit_count": archive_exact_glyph_hits,
            }
        )

    arm9 = args.arm9.read_bytes()
    selected_with_raw_hits = sorted(raw_code_hits)
    selected_with_exact_glyph_hits = sorted(exact_glyph_hits)
    compressed_count = wrapper_counts["compressed"]
    selected_with_text_hits = sorted(
        code for code, hits in selected_text_code_hits.items() if hits
    )
    selected_with_strong_text_hits = sorted(
        code
        for code, hits in selected_text_code_hits.items()
        if any(hit["confidence"] == "strong_terminal_script" for hit in hits)
    )
    report = {
        "schema_version": 1,
        "status": "pack_payloads_decoded_nonzero_text_collision_audited",
        "inputs": {
            "pack_dir": str(pack_dir),
            "head_inf_sha256": sha256(head_inf),
            "head_bin_sha256": sha256(head_bin),
            "mapping": {"path": str(args.mapping), "sha256": sha256(mapping_bytes)},
            "font": {"path": str(args.font), "sha256": sha256(font)},
            "alternate_font": {
                "path": str(args.alternate_font),
                "sha256": sha256(alternate_font),
            },
            "arm9": {"path": str(args.arm9), "sha256": sha256(arm9)},
        },
        "format": {
            "record_size": RECORD_SIZE,
            "name_field_size": NAME_SIZE,
            "archive_end_sentinel": "END + FFFFFFFF/FFFFFFFF",
            "resource_wrapper_prefix": WRAPPER_PREFIX.hex().upper(),
            "stored_wrapper_type": 0,
            "compressed_wrapper_type": 1,
        },
        "loader_evidence": {
            "arm9_string_offsets": loader_string_offsets(arm9),
            "interpretation": "ARM9 directly names the pack index, descriptor table, archive path template, and main fonts",
        },
        "font_consumer_evidence": {
            "runtime_switch": "%u command handler at ARM9 0x0202CD14-0x0202CD4C selects data/font.bin or data/font_lc.bin",
            "startup_load": "ARM9 0x02004B60 loads data/font_lc.bin",
            "production_build_requirement": "write every selected Hangul glyph to both 280000-byte fonts",
            "selected_mapping_count": len(all_mappings),
            "alternate_font_selected_original_states": dict(sorted(alternate_states.items())),
            "selected_indices_identical_between_fonts": equal_selected_glyphs,
            "selected_indices_different_between_fonts": len(all_mappings) - equal_selected_glyphs,
        },
        "directory_validation": {
            "archive_count": len(archives),
            "resource_count": resource_count,
            "all_descriptor_ranges_in_head_bin": True,
            "all_resource_ranges_in_archives": True,
            "all_offsets_0x20_aligned": True,
            "all_ranges_nonoverlapping": True,
        },
        "wrapper_population": {
            "counts": dict(sorted(wrapper_counts.items())),
            "compressed_resource_count": compressed_count,
            "compressed_payloads_decoded": compressed_count,
            "decoded_total_bytes": decoded_bytes,
            "compressed_decoded_identity": decoded_sha256_rows,
        },
        "decoded_text_population": {
            "strong_terminal_script_count": len(strong_text_candidates),
            "strong_terminal_scripts": strong_text_candidates,
            "weak_nul_cp932_candidate_count": weak_text_candidate_count,
            "selected_nonzero_codes_at_cp932_character_boundaries": len(selected_with_text_hits),
            "selected_nonzero_codes_with_text_hits": selected_with_text_hits,
            "selected_nonzero_codes_in_strong_terminal_scripts": len(selected_with_strong_text_hits),
            "selected_nonzero_codes_with_strong_text_hits": selected_with_strong_text_hits,
            "selected_nonzero_text_hit_locations": {
                code: selected_text_code_hits[code] for code in selected_with_text_hits
            },
            "weak_candidate_policy": "conservative_collision_signal_not_automatic_translation_record",
        },
        "selected_nonzero_mapping_audit": {
            "selected_nonzero_code_count": len(selected),
            "codes_with_raw_big_endian_pair_hits_in_bounded_resources": len(selected_with_raw_hits),
            "resources_with_any_raw_selected_code_hit": resources_with_raw_hits,
            "raw_pair_policy": "informational_only_binary_and_compressed_payloads_are_not_text_streams",
            "codes_with_exact_original_64_byte_glyph_payload_hits": len(selected_with_exact_glyph_hits),
            "exact_original_glyph_payload_hit_count": sum(exact_glyph_hits.values()),
            "exact_glyph_payload_locations": exact_glyph_locations,
            "per_code": [
                {
                    "code": row["code"],
                    "glyph_index": row["glyph_index"],
                    "mapping_origin": row["mapping_origin"],
                    "raw_pair_occurrence_count": raw_code_hits[row["code"]],
                    "raw_pair_resource_count": raw_code_entry_hits[row["code"]],
                    "exact_original_glyph_payload_hit_count": exact_glyph_hits[row["code"]],
                }
                for row in selected
            ],
        },
        "archives": archive_rows,
        "gates": {
            "pack_directory_structure": "pass",
            "bounded_resource_ranges": "pass",
            "stored_payload_visibility": "pass",
            "compressed_payload_visibility": "pass_arm9_0x02026B9C_decoder_reimplemented",
            "decoded_pack_text_collision": (
                "pass"
                if not selected_with_strong_text_hits
                else "fail_selected_nonzero_codes_present_in_strong_decoded_scripts"
            ),
            "nonzero_candidate_production_eligibility": (
                "pending_other_font_consumers_and_whole_game_runtime"
                if not selected_with_strong_text_hits
                else "fail_reallocate_colliding_codes"
            ),
        },
        "not_proved": [
            "that raw selected-code pairs inside binary resource bodies are decoded as text",
            "that font.bin, font_lc.bin, and db_font.bin share every renderer and code path",
            "that weak NUL-delimited CP932 decodes in graphical/model payloads are intentional text",
            "whole-game runtime safety after replacing all 194 selected nonzero glyphs",
        ],
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack-dir", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--alternate-font", type=Path, required=True)
    parser.add_argument("--arm9", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
