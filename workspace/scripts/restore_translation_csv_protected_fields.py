#!/usr/bin/env python3
"""Restore non-editable CSV fields from the exported Another Code baseline.

Spreadsheet programs can coerce zero-padded IDs and long hexadecimal strings
to numbers.  This utility keeps the translator-owned fields byte-for-byte as
parsed text while restoring every protected field from the canonical export.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import tempfile
from pathlib import Path

from audit_translation_csv import EDITABLE_FIELDS
from export_translation_csv import FIELDS


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {path}")
        return list(reader.fieldnames), list(reader)


def restore(baseline: Path, translated: Path, output: Path, backup: Path | None) -> dict:
    baseline_header, baseline_rows = read_rows(baseline)
    translated_header, translated_rows = read_rows(translated)
    if baseline_header != FIELDS or translated_header != FIELDS:
        raise ValueError("CSV header does not match the Another Code translation schema")
    if len(baseline_rows) != len(translated_rows):
        raise ValueError("baseline/translated row count mismatch")

    baseline_ids = [row["text_id"] for row in baseline_rows]
    translated_ids = [row["text_id"] for row in translated_rows]
    if baseline_ids != translated_ids or len(set(baseline_ids)) != len(baseline_ids):
        raise ValueError("baseline/translated text IDs are not identical and unique")

    protected_fields = [field for field in FIELDS if field not in EDITABLE_FIELDS]
    restored_counts = {field: 0 for field in protected_fields}
    repaired_rows: list[dict[str, str]] = []
    for baseline_row, translated_row in zip(baseline_rows, translated_rows, strict=True):
        repaired = dict(translated_row)
        for field in protected_fields:
            if repaired.get(field, "") != baseline_row[field]:
                restored_counts[field] += 1
            repaired[field] = baseline_row[field]
        repaired_rows.append(repaired)

    if backup is not None:
        backup.parent.mkdir(parents=True, exist_ok=True)
        if backup.exists():
            raise FileExistsError(f"backup already exists: {backup}")
        shutil.copy2(translated, backup)

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8-sig", newline="", delete=False, dir=output.parent, suffix=".csv"
    ) as handle:
        temp_path = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(repaired_rows)
    temp_path.replace(output)

    return {
        "rows": len(repaired_rows),
        "editable_fields_preserved": sorted(EDITABLE_FIELDS),
        "restored_fields": {key: value for key, value in restored_counts.items() if value},
        "restored_value_count": sum(restored_counts.values()),
        "backup": str(backup) if backup else None,
        "output": str(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--translated", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    print(restore(args.baseline, args.translated, args.output, args.backup))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
