#!/usr/bin/env python3
"""Another Code Korean CSV dialogue editor with NDS-style layout preview."""

from __future__ import annotations

import argparse
import ctypes
import csv
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageFont, ImageTk


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
DEFAULT_CSV = (
    PROJECT
    / "workspace"
    / "text"
    / "translation"
    / "another_code_full_text_v1_ko.csv"
)
PROFILE_PATH = HERE / "another_code_layout_profiles.json"
DEFAULT_FONT_CONFIG = PROJECT / "workspace" / "config" / "font_source.json"
DEFAULT_GAME_FONT = PROJECT / "extracted" / "nitrofs" / "data" / "font_lc.bin"

EDITABLE_FIELDS = {
    "translation_text",
    "translation_status",
    "speaker_names",
    "translator_notes",
}
REQUIRED_FIELDS = {
    "row_order",
    "text_id",
    "population",
    "scene_id",
    "component",
    "speaker_codes",
    "source_text",
    "translation_text",
    "translator_notes",
}
STATUS_VALUES = (
    "not_started",
    "draft",
    "needs_review",
    "needs_human_review",
    "reviewed",
    "approved",
)


def enable_windows_dpi_awareness() -> None:
    """Keep Tk geometry and screenshots in physical pixels on scaled displays."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


@dataclass(frozen=True)
class ControlToken:
    raw: str
    name: str
    argument: str | None
    start: int
    end: int


@dataclass(frozen=True)
class LayoutProfile:
    profile_id: str
    label: str
    max_columns: int
    max_lines: int
    alignment: str
    evidence: str
    geometry: str = "framed_2line"
    hard_limit: bool = True


@dataclass
class PreviewFrame:
    lines: list[str]
    speaker_code: str | None
    placement: str
    profile_id: str
    token_sequence: list[str]
    window_code: str | None = None


@dataclass(frozen=True)
class LayoutIssue:
    severity: str
    message: str


def scan_control_tokens(text: str) -> tuple[list[ControlToken], list[str]]:
    """Scan percent controls, including balanced parentheses inside arguments."""
    tokens: list[ControlToken] = []
    errors: list[str] = []
    index = 0
    while index < len(text):
        if text[index] != "%":
            index += 1
            continue
        start = index
        if index + 1 >= len(text) or not text[index + 1].isalpha():
            errors.append(f"위치 {index}: 잘못된 % 지시자")
            index += 1
            continue
        name = text[index + 1]
        index += 2
        argument = None
        if index < len(text) and text[index] == "(":
            depth = 0
            argument_start = index + 1
            while index < len(text):
                char = text[index]
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        index += 1
                        argument = text[argument_start : index - 1]
                        break
                index += 1
            else:
                errors.append(f"위치 {start}: %{name} 괄호가 닫히지 않음")
                index = len(text)
        raw = text[start:index]
        tokens.append(ControlToken(raw, name, argument, start, index))
    return tokens, errors


def directive_sequence(text: str) -> list[str]:
    return [token.raw for token in scan_control_tokens(text)[0]]


def functional_directive_sequence(text: str) -> list[str]:
    return [token.raw for token in scan_control_tokens(text)[0] if token.name.lower() != "r"]


def is_window_directive(token: str) -> bool:
    return len(token) > 3 and token[0] == "%" and token[1].lower() == "w" and token[2] == "("


def functional_directives_match(source: str, translation: str) -> bool:
    """Allow exact controls and safe ``%h%w(same)`` system-page splits."""
    expected = tuple(functional_directive_sequence(source))
    actual = tuple(functional_directive_sequence(translation))
    if expected == actual:
        return True

    @lru_cache(maxsize=None)
    def match(source_index: int, translation_index: int, active_window: str) -> bool:
        if source_index == len(expected) and translation_index == len(actual):
            return True
        if (
            translation_index + 1 < len(actual)
            and actual[translation_index].lower() == "%h"
            and is_window_directive(actual[translation_index + 1])
            and actual[translation_index + 1] == active_window
            and match(source_index, translation_index + 2, active_window)
        ):
            return True
        if (
            source_index < len(expected)
            and translation_index < len(actual)
            and expected[source_index] == actual[translation_index]
        ):
            token = actual[translation_index]
            next_window = token if is_window_directive(token) else active_window
            return match(source_index + 1, translation_index + 1, next_window)
        return False

    return match(0, 0, "")


def speaker_group(code: str | None) -> str | None:
    if not code:
        return None
    return code.split("_", 1)[0]


def speaker_placement(code: str | None) -> str:
    if code is None:
        return "system"
    return "bottom" if speaker_group(code) == "0" else "top"


def visible_cell_count(text: str) -> int:
    # The renderer consumes one glyph slot per encoded character in this path.
    return len(text.replace("\n", ""))


def visible_list_text(text: str) -> str:
    """Return one-line text for the list while hiding balanced control tokens."""
    tokens, _errors = scan_control_tokens(text)
    chunks: list[str] = []
    cursor = 0
    for token in tokens:
        chunks.append(text[cursor : token.start])
        cursor = token.end
    chunks.append(text[cursor:])
    return "".join(chunks).replace("\r", " ").replace("\n", " ").strip()


def normalize_window_code(argument: str | None) -> str | None:
    if argument is None or not argument.isdecimal():
        return None
    return argument.zfill(3)


def profile_id_for_window_code(window_code: str | None) -> str | None:
    if window_code is None:
        return None
    window_type = int(window_code) // 100
    if window_type in {1, 3, 5}:
        return "dialogue"
    if window_type in {2, 4}:
        return "system_message"
    if window_type == 0:
        return "direct_overlay"
    return None


def infer_profile_id(
    row: dict[str, str],
    has_speaker: bool = False,
    window_code: str | None = None,
) -> str:
    if row.get("population") in {"auxiliary", "isolated_candidate"}:
        return "auxiliary_ui"
    by_window = profile_id_for_window_code(window_code)
    if by_window:
        return by_window
    if row.get("component") == "arm9_overlay_000_file_000":
        return "freeform_screen"
    if has_speaker:
        return "dialogue"
    return "system_panel"


def load_profiles(path: Path = PROFILE_PATH) -> tuple[dict[str, LayoutProfile], dict[str, dict[str, object]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    profiles = {
        row["profile_id"]: LayoutProfile(
            profile_id=row["profile_id"],
            label=row["label"],
            max_columns=int(row["max_columns"]),
            max_lines=int(row["max_lines"]),
            alignment=row.get("alignment", "left"),
            evidence=row.get("evidence", ""),
            geometry=row.get("geometry", "framed_2line"),
            hard_limit=bool(row.get("hard_limit", True)),
        )
        for row in payload["profiles"]
    }
    return profiles, dict(payload.get("overrides", {}))


def save_overrides(path: Path, profiles: dict[str, LayoutProfile], overrides: dict[str, dict[str, object]]) -> None:
    payload = {
        "schema_version": 2,
        "notes": [
            "Main-script limits are derived from ARM9 geometry and the 12-pixel Hangul advance.",
            "Auxiliary consumers remain unenforced until their individual renderers are traced.",
            "Entry overrides are keyed by protected text_id and do not modify the translation CSV schema.",
        ],
        "profiles": [
            {
                "profile_id": p.profile_id,
                "label": p.label,
                "max_columns": p.max_columns,
                "max_lines": p.max_lines,
                "alignment": p.alignment,
                "evidence": p.evidence,
                "geometry": p.geometry,
                "hard_limit": p.hard_limit,
            }
            for p in profiles.values()
        ],
        "overrides": overrides,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_preview_frames(
    text: str,
    row: dict[str, str],
    forced_profile: str | None = None,
) -> list[PreviewFrame]:
    tokens, _errors = scan_control_tokens(text)
    frames: list[PreviewFrame] = []
    current_speaker: str | None = None
    current_window_code: str | None = None
    content_window_code: str | None = None
    content_started = False
    lines = [""]
    page_tokens: list[str] = []
    cursor = 0

    def add_literal(literal: str) -> None:
        nonlocal content_window_code, content_started
        if literal:
            visible = literal.replace("\r", "").replace("\n", "")
            if visible and not content_started:
                content_window_code = current_window_code
                content_started = True
            lines[-1] += visible

    def flush(force: bool = False) -> None:
        nonlocal lines, page_tokens, content_window_code, content_started
        if not force and not any(lines) and not page_tokens:
            return
        placement = speaker_placement(current_speaker)
        window_code = content_window_code if content_started else current_window_code
        profile_id = forced_profile or infer_profile_id(
            row, current_speaker is not None, window_code
        )
        frames.append(
            PreviewFrame(
                list(lines), current_speaker, placement, profile_id,
                list(page_tokens), window_code,
            )
        )
        lines = [""]
        page_tokens = []
        content_window_code = None
        content_started = False

    for token in tokens:
        add_literal(text[cursor : token.start])
        page_tokens.append(token.raw)
        if token.name == "w":
            normalized = normalize_window_code(token.argument)
            if normalized is not None:
                current_window_code = normalized
        elif token.name == "C" and token.argument:
            current_speaker = token.argument
        elif token.name == "r":
            lines.append("")
        elif token.name == "h":
            flush(True)
        elif token.name == "e":
            meaningful_tokens = [raw for raw in page_tokens if raw != token.raw]
            if any(lines) or meaningful_tokens:
                flush(True)
            else:
                lines = [""]
                page_tokens = []
        cursor = token.end
    add_literal(text[cursor:])
    flush(bool(any(lines) or page_tokens))
    return frames or [
        PreviewFrame(
            [""], current_speaker, "system",
            forced_profile or infer_profile_id(row, window_code=current_window_code),
            [], current_window_code,
        )
    ]


def layout_issues(frame: PreviewFrame, profile: LayoutProfile) -> list[LayoutIssue]:
    issues: list[LayoutIssue] = []
    if not profile.hard_limit:
        return issues
    if len(frame.lines) > profile.max_lines:
        issues.append(
            LayoutIssue("error", f"줄 수 {len(frame.lines)}/{profile.max_lines}: {len(frame.lines)-profile.max_lines}줄 초과")
        )
    for number, line in enumerate(frame.lines, 1):
        count = visible_cell_count(line)
        if count > profile.max_columns:
            issues.append(
                LayoutIssue("error", f"{number}줄 {count}/{profile.max_columns}자: {count-profile.max_columns}자 초과")
            )
    return issues


def effective_profile_for_frame(
    profile: LayoutProfile,
    frame: PreviewFrame,
    override: dict[str, object] | None = None,
) -> LayoutProfile:
    columns = profile.max_columns
    if profile.profile_id == "direct_overlay" and frame.window_code:
        columns = max(1, 19 - (int(frame.window_code) % 10))
    if override:
        columns = int(override.get("max_columns", columns))
    return LayoutProfile(
        profile.profile_id,
        profile.label,
        columns,
        int(override.get("max_lines", profile.max_lines)) if override else profile.max_lines,
        str(override.get("alignment", profile.alignment)) if override else profile.alignment,
        profile.evidence,
        profile.geometry,
        profile.hard_limit,
    )


def token_issues(source: str, translation: str) -> list[LayoutIssue]:
    issues: list[LayoutIssue] = []
    _source_tokens, source_errors = scan_control_tokens(source)
    _translated_tokens, translated_errors = scan_control_tokens(translation)
    for error in source_errors:
        issues.append(LayoutIssue("warning", "원문 지시자: " + error))
    for error in translated_errors:
        issues.append(LayoutIssue("error", "번역문 지시자: " + error))
    expected = functional_directive_sequence(source)
    actual = functional_directive_sequence(translation)
    if not functional_directives_match(source, translation):
        mismatch = next(
            (i for i, pair in enumerate(zip(expected, actual), 1) if pair[0] != pair[1]),
            min(len(expected), len(actual)) + 1,
        )
        issues.append(
            LayoutIssue(
                "error",
                f"기능 제어코드 순서 불일치: {mismatch}번째, 원문 {len(expected)}개 / 번역 {len(actual)}개 "
                "(%r 줄바꿈은 레이아웃 검사에서 별도 판정)",
            )
        )
    return issues


class DialogueDocument:
    def __init__(self, path: Path, headers: list[str], rows: list[dict[str, str]]):
        self.path = path
        self.headers = headers
        self.rows = rows
        self.by_id = {row["text_id"]: row for row in rows}

    @classmethod
    def load(cls, path: Path) -> "DialogueDocument":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            missing = sorted(REQUIRED_FIELDS - set(headers))
            if missing:
                raise ValueError("Another Code CSV 필수 열 누락: " + ", ".join(missing))
            rows = [dict(row) for row in reader]
        ids = [row["text_id"] for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError("CSV text_id가 중복됩니다")
        return cls(path, headers, rows)

    def save(self, path: Path | None = None, make_backup: bool = True) -> Path:
        target = path or self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        if make_backup and target.exists():
            backup_root = PROJECT / "backups" / "dialogue_editor"
            backup_root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.copy2(target, backup_root / f"{target.stem}_{stamp}{target.suffix}")
        descriptor, temporary_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=self.headers, lineterminator="\n")
                writer.writeheader()
                writer.writerows(self.rows)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        self.path = target
        return target


def unpack_game_glyph(data: bytes, index: int) -> Image.Image:
    start = index * 64
    raw = data[start : start + 64]
    if len(raw) != 64:
        raise ValueError(f"게임 폰트 글리프 범위 초과: {index}")
    image = Image.new("L", (16, 16), 0)
    offset = 0
    for tile_y, tile_x in ((0, 0), (0, 8), (8, 0), (8, 8)):
        for y in range(8):
            for group in range(2):
                value = raw[offset]
                offset += 1
                for pixel in range(4):
                    level = (value >> (6 - pixel * 2)) & 3
                    image.putpixel((tile_x + group * 4 + pixel, tile_y + y), level * 85)
    return image


def sjis_to_index(code: int) -> int:
    lead, trail = code >> 8, code & 0xFF
    if 0x81 <= lead <= 0x9F:
        lead_index = lead - 0x81
    elif 0xE0 <= lead <= 0xEF:
        lead_index = lead - 0xC1
    else:
        raise ValueError
    if not (0x40 <= trail <= 0xFC) or trail == 0x7F:
        raise ValueError
    trail_index = trail - 0x40 - (1 if trail >= 0x80 else 0)
    return lead_index * 188 + trail_index


class AnotherCodeFont:
    def __init__(self, font_config: Path = DEFAULT_FONT_CONFIG, game_font: Path = DEFAULT_GAME_FONT):
        config = json.loads(font_config.read_text(encoding="utf-8"))
        self.atlas_path = Path(config["source"]["path"])
        with Image.open(self.atlas_path) as opened:
            self.atlas = opened.convert("RGBA")
        self.cell_size = 16
        self.columns = self.atlas.width // self.cell_size
        colors = self.atlas.getcolors(maxcolors=self.atlas.width * self.atlas.height)
        self.background = max(colors, key=lambda item: item[0])[1] if colors else (0, 0, 0, 0)
        self.game_font = game_font.read_bytes()
        self.fallback = ImageFont.truetype(str(Path(config["source"]["path"]).parent / "Galmuri11-Condensed.ttf"), 12)
        self.cache: dict[str, Image.Image] = {}

    def glyph(self, char: str) -> Image.Image:
        if char in self.cache:
            return self.cache[char]
        if char == " ":
            glyph = Image.new("L", (16, 16), 0)
        elif 0xAC00 <= ord(char) <= 0xD7A3:
            index = ord(char) - 0xAC00
            x = (index % self.columns) * 16
            y = (index // self.columns) * 16
            cell = self.atlas.crop((x, y, x + 16, y + 16))
            glyph = Image.new("L", (16, 16), 0)
            for yy in range(16):
                for xx in range(16):
                    pixel = cell.getpixel((xx, yy))
                    if pixel[3] and pixel != self.background:
                        glyph.putpixel((xx, yy), 255)
        else:
            glyph = self._game_or_fallback_glyph(char)
        self.cache[char] = glyph
        return glyph

    def _game_or_fallback_glyph(self, char: str) -> Image.Image:
        try:
            encoded = char.encode("cp932")
            if len(encoded) == 2:
                return unpack_game_glyph(self.game_font, sjis_to_index(int.from_bytes(encoded, "big")))
        except (UnicodeEncodeError, ValueError):
            pass
        image = Image.new("L", (16, 16), 0)
        draw = ImageDraw.Draw(image)
        bbox = draw.textbbox((0, 0), char, font=self.fallback)
        x = (16 - (bbox[2] - bbox[0])) // 2 - bbox[0]
        y = (16 - (bbox[3] - bbox[1])) // 2 - bbox[1]
        draw.text((x, y), char, fill=255, font=self.fallback)
        return image


class ScreenRenderer:
    WIDTH = 256
    HEIGHT = 192

    def __init__(self, font: AnotherCodeFont):
        self.font = font

    def render(self, frame: PreviewFrame, profile: LayoutProfile, scale: int = 2) -> Image.Image:
        overflow = bool(layout_issues(frame, profile))
        image = Image.new("RGB", (self.WIDTH, self.HEIGHT), (17, 31, 43))
        draw = ImageDraw.Draw(image)
        advance = 12
        line_height = 16
        x0 = 8
        box_width = 240
        if profile.geometry == "framed_1line":
            box_height = 24
            y0 = 8 if frame.placement == "top" else 152
            inner_x = 24
            inner_y = y0 + 8
        elif profile.geometry == "framed_2line":
            box_height = 48
            y0 = 8 if frame.placement == "top" else 136
            inner_x = 24
            inner_y = y0 + 8
        elif profile.geometry == "direct_overlay":
            box_height = 0
            y0 = 0
            indent = int(frame.window_code or "000") % 10
            inner_x = 24 + indent * advance
            inner_y = 144
        elif profile.geometry == "fullscreen":
            box_height = 0
            y0 = 0
            inner_x = 24
            visible_lines = min(len(frame.lines), profile.max_lines)
            inner_y = max(8, (self.HEIGHT - visible_lines * line_height) // 2)
        else:
            # Auxiliary strings come from heuristic tables whose consumers are
            # not yet traced. Render a neutral guide without claiming a limit.
            box_height = 32
            y0 = 80
            inner_x = 16
            inner_y = y0 + 8

        border = (221, 66, 66) if overflow else (72, 93, 109)
        if box_height:
            draw.rounded_rectangle(
                (x0, y0, x0 + box_width, y0 + box_height),
                radius=4, fill=(245, 241, 225), outline=border, width=2,
            )
        for line_index, line in enumerate(frame.lines[: profile.max_lines]):
            line_width = visible_cell_count(line) * advance
            if profile.alignment == "center":
                start_x = (self.WIDTH - line_width) // 2
            else:
                start_x = inner_x
            baseline_y = inner_y + line_index * line_height
            for char_index, char in enumerate(line):
                gx = start_x + char_index * advance
                right_edge = 232 if profile.geometry.startswith("framed_") else self.WIDTH
                if gx + 16 > right_edge:
                    break
                glyph = self.font.glyph(char)
                ink_color = (
                    (238, 238, 238)
                    if profile.geometry in {"direct_overlay", "fullscreen"}
                    else (37, 37, 37)
                )
                ink = Image.new("RGB", glyph.size, ink_color)
                image.paste(ink, (gx, baseline_y), glyph)
        label_font = ImageFont.load_default()
        label = (
            "PROTAGONIST / BOTTOM"
            if frame.placement == "bottom"
            else "OTHER / TOP"
            if frame.placement == "top"
            else "SYSTEM"
        )
        draw.text((4, 178), label, fill=(197, 213, 224), font=label_font)
        if overflow:
            draw.text((156, 178), "OVERFLOW", fill=(255, 95, 95), font=label_font)
        return image.resize((self.WIDTH * scale, self.HEIGHT * scale), Image.Resampling.NEAREST)


def entry_validation(
    row: dict[str, str],
    profiles: dict[str, LayoutProfile],
    override: dict[str, object] | None = None,
) -> list[LayoutIssue]:
    text = row.get("translation_text", "")
    issues = token_issues(row.get("source_text", ""), text)
    forced = str(override["profile_id"]) if override and override.get("profile_id") else None
    frames = parse_preview_frames(text, row, forced)
    for index, frame in enumerate(frames, 1):
        base = profiles[frame.profile_id]
        profile = effective_profile_for_frame(base, frame, override)
        for issue in layout_issues(frame, profile):
            issues.append(LayoutIssue(issue.severity, f"{index}페이지: {issue.message}"))
    return issues


class DialogueEditor(tk.Tk):
    def __init__(self, initial_path: Path):
        super().__init__()
        self.title("Another Code 한글 대사 편집기")
        self.geometry("1800x900")
        self.minsize(1450, 760)
        self.profiles, self.overrides = load_profiles()
        self.profile_labels = {p.label: p.profile_id for p in self.profiles.values()}
        self.document: DialogueDocument | None = None
        self.current_index: int | None = None
        self.current_frames: list[PreviewFrame] = []
        self.page_index = 0
        self.dirty = False
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.font_renderer = AnotherCodeFont()
        self.screen_renderer = ScreenRenderer(self.font_renderer)
        self._build_ui()
        self.bind_all("<Control-s>", lambda _event: self.save_file())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.load_file(initial_path)
        self.update_idletasks()
        self._position_main_sash()

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, padding=6)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="CSV 열기", command=self.open_file).pack(side="left")
        ttk.Button(toolbar, text="저장 Ctrl+S", command=self.save_file).pack(side="left", padx=5)
        ttk.Button(toolbar, text="다른 이름으로 저장", command=lambda: self.save_file(True)).pack(side="left")
        self.file_label = ttk.Label(toolbar, text="", anchor="w")
        self.file_label.pack(side="left", padx=12, fill="x", expand=True)
        self.summary_label = ttk.Label(toolbar, text="")
        self.summary_label.pack(side="right")

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.main_panes = body
        left = ttk.Frame(body, padding=4)
        center = ttk.Frame(body, padding=4)
        right = ttk.Frame(body, padding=4)
        body.add(left, weight=7)
        body.add(center, weight=5)
        body.add(right, weight=6)

        filters = ttk.Frame(left)
        filters.pack(fill="x", pady=(0, 5))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh_tree())
        ttk.Entry(filters, textvariable=self.search_var).pack(side="left", fill="x", expand=True)
        self.issue_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(filters, text="문제만", variable=self.issue_only_var, command=self.refresh_tree).pack(side="left", padx=4)

        tree_area = ttk.Frame(left)
        tree_area.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_area,
            columns=("row", "scene", "kind", "speaker", "source", "translation"),
            show="headings",
            selectmode="browse",
        )
        for key, title, width in (
            ("row", "#", 40), ("scene", "Scene", 88), ("kind", "창", 58),
            ("speaker", "화자", 82), ("source", "원문", 220), ("translation", "번역", 220),
        ):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, stretch=key in {"source", "translation"})
        self.tree.tag_configure("error", foreground="#b00020")
        self.tree.tag_configure("warning", foreground="#a05a00")
        self.tree.tag_configure("untranslated", foreground="#777777")
        vertical_scroll = ttk.Scrollbar(tree_area, orient="vertical", command=self.tree.yview)
        horizontal_scroll = ttk.Scrollbar(left, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical_scroll.set, xscrollcommand=horizontal_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vertical_scroll.pack(side="right", fill="y")
        horizontal_scroll.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", self._select_entry)

        meta = ttk.LabelFrame(center, text="항목 정보", padding=6)
        meta.pack(fill="x")
        self.meta_label = ttk.Label(meta, text="", justify="left", wraplength=470)
        self.meta_label.pack(fill="x")

        source_frame = ttk.LabelFrame(center, text="원문 (보호)", padding=5)
        source_frame.pack(fill="x", pady=(6, 0))
        self.source_text = tk.Text(source_frame, height=7, wrap="word", font=("Malgun Gothic", 10), background="#f1f1f1")
        self.source_text.pack(fill="x")
        self.source_text.configure(state="disabled")

        edit_frame = ttk.LabelFrame(center, text="한국어 번역 — Enter: %r 줄바꿈", padding=5)
        edit_frame.pack(fill="both", expand=True, pady=(6, 0))
        controls = ttk.Frame(edit_frame)
        controls.pack(fill="x")
        ttk.Button(controls, text="줄바꿈 %r", command=lambda: self.insert_token("%r")).pack(side="left")
        ttk.Button(controls, text="다음 창 %h", command=lambda: self.insert_token("%h")).pack(side="left", padx=4)
        ttk.Label(controls, text="상태").pack(side="left", padx=(10, 4))
        self.status_var = tk.StringVar()
        self.status_combo = ttk.Combobox(controls, textvariable=self.status_var, values=STATUS_VALUES, width=17)
        self.status_combo.pack(side="left")
        self.status_combo.bind("<<ComboboxSelected>>", lambda _event: self._mark_dirty())
        speaker_controls = ttk.Frame(edit_frame)
        speaker_controls.pack(fill="x", pady=(5, 0))
        ttk.Label(speaker_controls, text="화자명 메모").pack(side="left", padx=(0, 4))
        self.speaker_names_var = tk.StringVar()
        speaker_entry = ttk.Entry(speaker_controls, textvariable=self.speaker_names_var, width=25)
        speaker_entry.pack(side="left", fill="x", expand=True)
        speaker_entry.bind("<KeyRelease>", lambda _event: self._mark_dirty())
        self.translation_text = tk.Text(edit_frame, height=14, wrap="word", undo=True, font=("Malgun Gothic", 12))
        self.translation_text.pack(fill="both", expand=True, pady=(5, 0))
        self.translation_text.bind("<KeyRelease>", self._translation_changed)
        self.translation_text.bind("<Return>", self._enter_as_line_break)

        notes = ttk.LabelFrame(center, text="번역 메모", padding=5)
        notes.pack(fill="x", pady=(6, 0))
        self.notes_text = tk.Text(notes, height=4, wrap="word", font=("Malgun Gothic", 10))
        self.notes_text.pack(fill="x")
        self.notes_text.bind("<KeyRelease>", lambda _event: self._mark_dirty())

        preview = ttk.LabelFrame(right, text="NDS 대사창 미리보기", padding=5)
        preview.pack(fill="both", expand=True)
        profile_row = ttk.Frame(preview)
        profile_row.pack(fill="x")
        ttk.Label(profile_row, text="레이아웃").pack(side="left")
        self.profile_var = tk.StringVar(value="자동")
        labels = ["자동"] + [p.label for p in self.profiles.values()]
        self.profile_combo = ttk.Combobox(profile_row, textvariable=self.profile_var, values=labels, state="readonly", width=25)
        self.profile_combo.pack(side="left", padx=4)
        self.profile_combo.bind("<<ComboboxSelected>>", lambda _event: self._profile_changed())
        ttk.Button(profile_row, text="규칙 저장", command=self.save_layout_override).pack(side="right")

        limit_row = ttk.Frame(preview)
        limit_row.pack(fill="x", pady=(5, 0))
        ttk.Label(limit_row, text="한 줄").pack(side="left")
        self.columns_var = tk.IntVar(value=17)
        ttk.Spinbox(limit_row, from_=1, to=40, textvariable=self.columns_var, width=4, command=self._limit_changed).pack(side="left")
        ttk.Label(limit_row, text="자 · 한 창").pack(side="left", padx=(2, 2))
        self.lines_var = tk.IntVar(value=2)
        ttk.Spinbox(limit_row, from_=1, to=8, textvariable=self.lines_var, width=4, command=self._limit_changed).pack(side="left")
        ttk.Label(limit_row, text="줄").pack(side="left", padx=(2, 8))
        ttk.Button(limit_row, text="◀", width=3, command=lambda: self.change_page(-1)).pack(side="right")
        ttk.Button(limit_row, text="▶", width=3, command=lambda: self.change_page(1)).pack(side="right")
        self.page_label = ttk.Label(limit_row, text="1/1")
        self.page_label.pack(side="right", padx=6)
        self.profile_note = ttk.Label(preview, text="", anchor="w", justify="left", wraplength=530)
        self.profile_note.pack(fill="x", pady=(4, 2))
        self.preview_canvas = tk.Canvas(preview, width=512, height=384, background="#111f2b", highlightthickness=0)
        self.preview_canvas.pack(anchor="center")
        self.validation_label = ttk.Label(preview, text="", justify="left", wraplength=530)
        self.validation_label.pack(fill="x", pady=(4, 0))

    def _position_main_sash(self) -> None:
        self.update_idletasks()
        width = self.main_panes.winfo_width()
        if width >= 1200:
            first = min(720, max(560, int(width * 0.39)))
            second = min(width - 550, first + 500)
            self.main_panes.sashpos(0, first)
            self.update_idletasks()
            self.main_panes.sashpos(1, second)

    def open_file(self) -> None:
        selected = filedialog.askopenfilename(initialdir=str(DEFAULT_CSV.parent), filetypes=(("CSV", "*.csv"),))
        if selected:
            self.load_file(Path(selected))

    def load_file(self, path: Path) -> None:
        try:
            self.document = DialogueDocument.load(path)
        except Exception as error:
            messagebox.showerror("열기 실패", str(error))
            return
        self.file_label.configure(text=str(path))
        self.current_index = None
        self.dirty = False
        self.refresh_tree()
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])
            self.tree.see(children[0])
        self._update_summary()

    def _current_row(self) -> dict[str, str] | None:
        if self.document is None or self.current_index is None:
            return None
        return self.document.rows[self.current_index]

    def _commit_current(self) -> None:
        row = self._current_row()
        if row is None:
            return
        row["translation_text"] = self.translation_text.get("1.0", "end-1c")
        row["translation_status"] = self.status_var.get()
        row["speaker_names"] = self.speaker_names_var.get()
        row["translator_notes"] = self.notes_text.get("1.0", "end-1c")

    def refresh_tree(self) -> None:
        if self.document is None:
            return
        self._commit_current()
        selected_id = self._current_row()["text_id"] if self._current_row() else None
        self.tree.delete(*self.tree.get_children())
        query = self.search_var.get().strip().casefold()
        issue_count = 0
        for index, row in enumerate(self.document.rows):
            override = self.overrides.get(row["text_id"])
            issues = entry_validation(row, self.profiles, override)
            if issues:
                issue_count += 1
            haystack = " ".join(
                row.get(key, "")
                for key in ("row_order", "text_id", "scene_id", "component", "speaker_codes", "speaker_names", "source_text", "translation_text")
            ).casefold()
            if query and query not in haystack:
                continue
            if self.issue_only_var.get() and not issues:
                continue
            profile_id = (
                str(override.get("profile_id"))
                if override and override.get("profile_id")
                else parse_preview_frames(row.get("translation_text") or row.get("source_text", ""), row)[0].profile_id
            )
            profile = self.profiles.get(profile_id, self.profiles["system_message"])
            speaker = row.get("speaker_names") or row.get("speaker_codes") or "시스템"
            source_preview = visible_list_text(row.get("source_text", ""))
            translation_preview = visible_list_text(row.get("translation_text", ""))
            tags = ()
            if any(issue.severity == "error" for issue in issues):
                tags = ("error",)
            elif issues:
                tags = ("warning",)
            elif not row.get("translation_text", "").strip():
                tags = ("untranslated",)
            iid = str(index)
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    row["row_order"],
                    row["scene_id"],
                    profile.label.split(" ", 1)[0],
                    speaker,
                    source_preview[:120],
                    translation_preview[:120],
                ),
                tags=tags,
            )
            if selected_id and row["text_id"] == selected_id:
                self.tree.selection_set(iid)
        self.summary_label.configure(text=f"전체 {len(self.document.rows):,} · 문제 {issue_count:,}")

    def _select_entry(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        new_index = int(selected[0])
        if self.current_index is not None and new_index != self.current_index:
            self._commit_current()
        self.current_index = new_index
        row = self._current_row()
        if row is None:
            return
        meta = (
            f"ID: {row['text_id']}   Row: {row['row_order']}   Scene: {row['scene_id']}\n"
            f"Component: {row['component']}   Population: {row['population']}   Speaker codes: {row.get('speaker_codes') or '-'}\n"
            f"Events: {row.get('event_ids') or '-'}   Images: {row.get('image_ids') or '-'}"
        )
        self.meta_label.configure(text=meta)
        self._set_text(self.source_text, row["source_text"], readonly=True)
        self._set_text(self.translation_text, row["translation_text"])
        self._set_text(self.notes_text, row.get("translator_notes", ""))
        self.status_var.set(row.get("translation_status", ""))
        self.speaker_names_var.set(row.get("speaker_names", ""))
        override = self.overrides.get(row["text_id"])
        if override:
            profile_id = str(
                override.get("profile_id")
                or parse_preview_frames(row.get("translation_text") or row.get("source_text", ""), row)[0].profile_id
            )
            self.profile_var.set(self.profiles[profile_id].label)
            self.columns_var.set(int(override.get("max_columns", self.profiles[profile_id].max_columns)))
            self.lines_var.set(int(override.get("max_lines", self.profiles[profile_id].max_lines)))
        else:
            self.profile_var.set("자동")
            first_frame = parse_preview_frames(row.get("translation_text") or row.get("source_text", ""), row)[0]
            profile = effective_profile_for_frame(self.profiles[first_frame.profile_id], first_frame)
            self.columns_var.set(profile.max_columns)
            self.lines_var.set(profile.max_lines)
        self.page_index = 0
        self._update_preview()

    @staticmethod
    def _set_text(widget: tk.Text, value: str, readonly: bool = False) -> None:
        if readonly:
            widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        if readonly:
            widget.configure(state="disabled")

    def _translation_changed(self, _event=None) -> None:
        self._mark_dirty()
        self._update_preview()

    def _enter_as_line_break(self, _event=None):
        self.insert_token("%r")
        return "break"

    def insert_token(self, token: str) -> None:
        self.translation_text.insert("insert", token)
        self._translation_changed()

    def _mark_dirty(self) -> None:
        self.dirty = True
        if not self.title().endswith(" *"):
            self.title(self.title() + " *")

    def _selected_profile_id(self, row: dict[str, str]) -> str | None:
        return None if self.profile_var.get() == "자동" else self.profile_labels[self.profile_var.get()]

    def _effective_profile(self, frame: PreviewFrame) -> LayoutProfile:
        base = self.profiles[frame.profile_id]
        if self.profile_var.get() == "자동":
            effective = effective_profile_for_frame(base, frame)
            self.columns_var.set(effective.max_columns)
            self.lines_var.set(effective.max_lines)
            return effective
        return LayoutProfile(
            base.profile_id, base.label, self.columns_var.get(), self.lines_var.get(),
            base.alignment, base.evidence, base.geometry, base.hard_limit,
        )

    def _profile_changed(self) -> None:
        row = self._current_row()
        if row is None:
            return
        profile_id = self._selected_profile_id(row) or parse_preview_frames(
            self.translation_text.get("1.0", "end-1c"), row
        )[0].profile_id
        profile = self.profiles[profile_id]
        self.columns_var.set(profile.max_columns)
        self.lines_var.set(profile.max_lines)
        self._update_preview()

    def _limit_changed(self) -> None:
        self._update_preview()

    def _update_preview(self) -> None:
        row = self._current_row()
        if row is None:
            return
        text = self.translation_text.get("1.0", "end-1c")
        forced = self._selected_profile_id(row)
        self.current_frames = parse_preview_frames(text, row, forced)
        self.page_index = min(self.page_index, len(self.current_frames) - 1)
        frame = self.current_frames[self.page_index]
        profile = self._effective_profile(frame)
        image = self.screen_renderer.render(frame, profile)
        self.preview_photo = ImageTk.PhotoImage(image)
        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(0, 0, anchor="nw", image=self.preview_photo)
        self.page_label.configure(text=f"{self.page_index + 1}/{len(self.current_frames)}")
        speaker = frame.speaker_code or "없음"
        placement = {"top": "위쪽 대사창", "bottom": "아래쪽 대사창", "system": "시스템 창"}[frame.placement]
        self.profile_note.configure(
            text=(
                f"{profile.label} · "
                f"{'검사 보류' if not profile.hard_limit else f'{profile.max_columns}자×{profile.max_lines}줄'}"
                f" · %w({frame.window_code or '-'}) · 화자 {speaker} → {placement}\n"
                f"근거: {profile.evidence}"
            )
        )
        issues = token_issues(row["source_text"], text) + layout_issues(frame, profile)
        if issues:
            self.validation_label.configure(text="\n".join("⚠ " + issue.message for issue in issues), foreground="#b00020")
        else:
            counts = ", ".join(
                f"{visible_cell_count(line)}/{profile.max_columns}" for line in frame.lines
            )
            verdict = "검사 보류" if not profile.hard_limit else "통과"
            color = "#a05a00" if not profile.hard_limit else "#176b2c"
            self.validation_label.configure(
                text=f"{verdict} · 줄별 글자수: {counts or '0'}", foreground=color
            )

    def change_page(self, delta: int) -> None:
        if not self.current_frames:
            return
        self.page_index = (self.page_index + delta) % len(self.current_frames)
        self._update_preview()

    def save_layout_override(self) -> None:
        row = self._current_row()
        if row is None:
            return
        profile_id = self._selected_profile_id(row) or parse_preview_frames(
            self.translation_text.get("1.0", "end-1c"), row
        )[0].profile_id
        self.overrides[row["text_id"]] = {
            "profile_id": profile_id,
            "max_columns": self.columns_var.get(),
            "max_lines": self.lines_var.get(),
            "alignment": self.profiles[profile_id].alignment,
        }
        save_overrides(PROFILE_PATH, self.profiles, self.overrides)
        self._mark_dirty()
        self._update_preview()
        messagebox.showinfo("레이아웃 규칙", f"{row['text_id']} 규칙을 저장했습니다.")

    def save_file(self, save_as: bool = False) -> None:
        if self.document is None:
            return
        self._commit_current()
        path = self.document.path
        if save_as:
            selected = filedialog.asksaveasfilename(initialdir=str(path.parent), initialfile=path.name, defaultextension=".csv", filetypes=(("CSV", "*.csv"),))
            if not selected:
                return
            path = Path(selected)
        all_token_errors = []
        for row in self.document.rows:
            issues = token_issues(row["source_text"], row["translation_text"])
            if any(issue.severity == "error" for issue in issues):
                all_token_errors.append(row["text_id"])
        if all_token_errors:
            answer = messagebox.askyesno(
                "제어코드 오류",
                f"제어코드가 원문과 다른 항목이 {len(all_token_errors)}개 있습니다.\n"
                "현재 CSV는 빌드 적격이 아닙니다. 그래도 작업본을 저장할까요?",
            )
            if not answer:
                return
        try:
            saved = self.document.save(path)
        except Exception as error:
            messagebox.showerror("저장 실패", str(error))
            return
        self.dirty = False
        self.title("Another Code 한글 대사 편집기")
        self.file_label.configure(text=str(saved))
        self.refresh_tree()
        messagebox.showinfo("저장 완료", f"저장했습니다.\n{saved}\n\n기존 파일은 backups/dialogue_editor에 백업됩니다.")

    def _update_summary(self) -> None:
        if self.document is None:
            return
        translated = sum(bool(row.get("translation_text", "").strip()) for row in self.document.rows)
        self.summary_label.configure(text=f"번역 {translated:,}/{len(self.document.rows):,}")

    def _close(self) -> None:
        if self.dirty and not messagebox.askyesno("종료", "저장하지 않은 변경이 있습니다. 종료할까요?"):
            return
        self.destroy()


def check_document(path: Path) -> dict[str, object]:
    profiles, overrides = load_profiles()
    document = DialogueDocument.load(path)
    error_rows = 0
    overflow_rows = 0
    token_error_rows = 0
    bottom_frames = 0
    top_frames = 0
    system_frames = 0
    for row in document.rows:
        override = overrides.get(row["text_id"])
        issues = entry_validation(row, profiles, override)
        token_errors_for_row = token_issues(row["source_text"], row["translation_text"])
        if any(issue.severity == "error" for issue in issues):
            error_rows += 1
        if any("초과" in issue.message for issue in issues):
            overflow_rows += 1
        if any(issue.severity == "error" for issue in token_errors_for_row):
            token_error_rows += 1
        forced = str(override["profile_id"]) if override and override.get("profile_id") else None
        for frame in parse_preview_frames(row["translation_text"], row, forced):
            if frame.placement == "bottom":
                bottom_frames += 1
            elif frame.placement == "top":
                top_frames += 1
            else:
                system_frames += 1
    font = AnotherCodeFont()
    sample_row = next(row for row in document.rows if "한" in row.get("translation_text", "") or "가" in row.get("translation_text", ""))
    sample_frame = parse_preview_frames(sample_row["translation_text"], sample_row)[0]
    sample_profile = effective_profile_for_frame(profiles[sample_frame.profile_id], sample_frame)
    sample_image = ScreenRenderer(font).render(sample_frame, sample_profile)
    return {
        "status": "editor_static_check_complete",
        "csv": str(path),
        "rows": len(document.rows),
        "translated_rows": sum(bool(row["translation_text"].strip()) for row in document.rows),
        "token_error_rows": token_error_rows,
        "layout_overflow_rows": overflow_rows,
        "rows_with_errors": error_rows,
        "frames": {"protagonist_bottom": bottom_frames, "other_top": top_frames, "system": system_frames},
        "font_atlas": str(font.atlas_path),
        "sample_preview_size": list(sample_image.size),
        "runtime_note": "Preview limits do not replace emulator QA for unresolved consumers.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--check", action="store_true", help="CSV/font/layout static check without opening the GUI")
    args = parser.parse_args()
    if args.check:
        print(json.dumps(check_document(args.file), ensure_ascii=False, indent=2))
        return 0
    enable_windows_dpi_awareness()
    app = DialogueEditor(args.file)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
