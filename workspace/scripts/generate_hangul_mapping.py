#!/usr/bin/env python3
"""Generate a deterministic PoC Hangul mapping from an approved code pool."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from analyze_game_font import index_to_sjis


def first_occurrence_hangul(entries: list[dict]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        for char in entry["translation_text"]:
            if "가" <= char <= "힣" and char not in seen:
                seen.add(char)
                result.append(char)
    return result


def allocate_mappings(
    characters: list[str], candidates: list[tuple[int, int]], existing: dict | None
) -> list[dict]:
    preserved: dict[str, tuple[int, int]] = {}
    if existing is not None:
        candidate_pairs = set(candidates)
        for row in existing["mappings"]:
            pair = (int(row["code"], 16), row["glyph_index"])
            if row["character"] in characters:
                if pair not in candidate_pairs:
                    raise ValueError(f"existing mapping is outside the available pool: {row}")
                preserved[row["character"]] = pair
    reserved = set(preserved.values())
    available = iter(pair for pair in candidates if pair not in reserved)
    mappings = []
    for char in characters:
        code, index = preserved[char] if char in preserved else next(available, (None, None))
        if code is None:
            raise ValueError("Hangul demand exceeds the approved PoC pool")
        mappings.append({"character": char, "code": f"{code:04X}", "glyph_index": index})
    return mappings


def generate(args: argparse.Namespace) -> None:
    batch = json.loads(args.batch.read_text(encoding="utf-8"))
    pool = json.loads(args.pool.read_text(encoding="utf-8"))
    font = json.loads(args.font_config.read_text(encoding="utf-8"))
    scripts = json.loads(args.script_report.read_text(encoding="utf-8"))["records"]
    auxiliary = json.loads(args.aux_report.read_text(encoding="utf-8"))["records"]
    if batch["source_sha256"] != pool["source_sha256"]:
        raise ValueError("batch/code-pool source identity mismatch")
    if font.get("selection_status") != "approved_by_user_runtime_qa":
        raise ValueError("font selection is not runtime-approved")

    used = {
        bytes.fromhex(row["raw_hex"])[offset : offset + 2]
        for row in scripts + auxiliary
        for offset in range(max(0, len(bytes.fromhex(row["raw_hex"])) - 1))
    }
    candidates = []
    for index in range(pool["start_glyph_index"], pool["end_glyph_index"] - 1, -1):
        code = index_to_sjis(index)
        raw = code.to_bytes(2, "big")
        if raw not in used:
            candidates.append((code, index))

    characters = first_occurrence_hangul(batch["entries"])
    if len(characters) > len(candidates):
        raise ValueError(
            f"Hangul demand {len(characters)} exceeds approved PoC pool {len(candidates)}"
        )
    existing = None
    if args.existing_mapping is not None:
        existing = json.loads(args.existing_mapping.read_text(encoding="utf-8"))
        for key, expected in (
            ("source_sha256", batch["source_sha256"]),
            ("pool_id", pool["pool_id"]),
            ("font_sha256", font["source"]["sha256"]),
        ):
            if existing.get(key) != expected:
                raise ValueError(f"existing mapping {key} mismatch")
    mappings = allocate_mappings(characters, candidates, existing)
    report = {
        "schema_version": 1,
        "mapping_id": f"{batch['batch_id']}-mapping",
        "status": "development_poc_mapping_not_whole_game_approved",
        "source_sha256": batch["source_sha256"],
        "batch_id": batch["batch_id"],
        "pool_id": pool["pool_id"],
        "font_sha256": font["source"]["sha256"],
        "character_count": len(characters),
        "available_pool_count": len(candidates),
        "mappings": mappings,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--font-config", type=Path, required=True)
    parser.add_argument("--script-report", type=Path, required=True)
    parser.add_argument("--aux-report", type=Path, required=True)
    parser.add_argument("--existing-mapping", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    generate(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
