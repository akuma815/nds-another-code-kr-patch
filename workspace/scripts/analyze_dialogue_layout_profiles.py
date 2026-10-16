#!/usr/bin/env python3
"""Measure Another Code text-window limits from ARM9 geometry and the CSV corpus."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]
EDITOR_DIR = PROJECT / "tools" / "dialogue_editor"
sys.path.insert(0, str(EDITOR_DIR))

from another_code_dialogue_editor import (  # noqa: E402
    effective_profile_for_frame,
    load_profiles,
    parse_preview_frames,
    visible_cell_count,
)


DEFAULT_CSV = (
    PROJECT / "workspace" / "text" / "translation" /
    "another_code_full_text_v1_ko.csv"
)
DEFAULT_OUTPUT = PROJECT / "workspace" / "analysis" / "dialogue_layout_profiles_v1.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def meaningful_line_count(lines: list[str]) -> int:
    meaningful = [index for index, line in enumerate(lines, 1) if line.strip()]
    return max(meaningful, default=0)


def build_report(csv_path: Path) -> dict[str, object]:
    profiles, _overrides = load_profiles()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    stats: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "page_count": 0,
            "source_observed_max_columns": 0,
            "source_observed_max_meaningful_lines": 0,
            "window_codes": Counter(),
            "example_text_ids": [],
        }
    )
    for row in rows:
        for frame in parse_preview_frames(row["source_text"], row):
            profile = effective_profile_for_frame(profiles[frame.profile_id], frame)
            bucket = stats[frame.profile_id]
            bucket["page_count"] += 1
            observed_columns = max((visible_cell_count(line) for line in frame.lines), default=0)
            observed_lines = meaningful_line_count(frame.lines)
            if observed_columns > bucket["source_observed_max_columns"]:
                bucket["source_observed_max_columns"] = observed_columns
                bucket["example_text_ids"] = [row["text_id"]]
            elif (
                observed_columns == bucket["source_observed_max_columns"]
                and len(bucket["example_text_ids"]) < 5
            ):
                bucket["example_text_ids"].append(row["text_id"])
            bucket["source_observed_max_meaningful_lines"] = max(
                bucket["source_observed_max_meaningful_lines"], observed_lines
            )
            bucket["window_codes"][frame.window_code or "none"] += 1

    profile_rows = []
    for profile_id, profile in profiles.items():
        bucket = stats.get(profile_id, {})
        profile_rows.append(
            {
                "profile_id": profile_id,
                "label": profile.label,
                "calculated_max_hangul_columns": (
                    None
                    if not profile.hard_limit
                    else "19-n"
                    if profile_id == "direct_overlay"
                    else profile.max_columns
                ),
                "calculated_max_lines": (
                    profile.max_lines if profile.hard_limit else None
                ),
                "hard_limit": profile.hard_limit,
                "geometry": profile.geometry,
                "evidence": profile.evidence,
                "corpus": {
                    "page_count": int(bucket.get("page_count", 0)),
                    "source_observed_max_columns": int(
                        bucket.get("source_observed_max_columns", 0)
                    ),
                    "source_observed_max_meaningful_lines": int(
                        bucket.get("source_observed_max_meaningful_lines", 0)
                    ),
                    "window_codes": dict(
                        sorted(bucket.get("window_codes", {}).items())
                    ),
                    "example_text_ids": bucket.get("example_text_ids", []),
                },
            }
        )

    return {
        "schema_version": 1,
        "status": "static_renderer_geometry_and_corpus_measurement_complete",
        "translation_ssot": {
            "path": str(csv_path),
            "sha256": sha256(csv_path),
            "modified": False,
            "row_count": len(rows),
        },
        "renderer_geometry": {
            "screen_width_px": 256,
            "approved_hangul_advance_px": 12,
            "glyph_cell_px": [16, 16],
            "glyph_draw_x": "24 + 12*window_unit + line_x_offset",
            "line_advance_px": 16,
            "framed_inner_width_px": 208,
            "framed_capacity_formula": "floor((208-16)/12)+1 = 17",
            "direct_overlay_capacity_formula": "floor((256-(24+12*n)-16)/12)+1 = 19-n",
            "arm9_evidence": [
                "0x0202A6E4..0x0202A818 window tile geometry",
                "0x0202CB9C..0x0202CBBC %r reset X and add 16px Y",
                "0x0202D43C..0x0202D4DC glyph origin and 12px advance",
            ],
        },
        "profiles": profile_rows,
        "limitations": [
            "Auxiliary records are heuristic CP932 table candidates and do not share one proven renderer.",
            "Static geometry does not replace runtime QA for each untraced auxiliary consumer.",
            "The translation CSV is preserved as the SSOT and was not rewritten.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_report(args.csv.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
