#!/usr/bin/env python3
"""Build self-contained figures for the font/opening localization retrospective."""

from __future__ import annotations

import csv
import json
import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from build_graphics_edit_canvases import build as build_edit_canvases
from render_pack_index_previews import render_index_preview


PROJECT = Path(__file__).resolve().parents[2]
WORKSPACE = PROJECT / "workspace"
OUTPUT = PROJECT / "docs" / "assets" / "font_opening_retrospective"
MAPPING = WORKSPACE / "config" / "hangul_mapping_full_candidate_20260829_v44.json"
FONT_CONFIG = WORKSPACE / "config" / "font_source.json"
PACK_ROOT = WORKSPACE / "graphics" / "pack_v2"
GRAPHICS_CONFIG = WORKSPACE / "config" / "graphics_edit_transforms_v39_cumulative.csv"
FINAL_INDEXED = WORKSPACE / "graphics" / "translate_final_v39_cumulative" / "indexed"
RUNTIME_POC = PROJECT / "qa" / "runtime" / "font-visibility-001" / "runtime_pass_han_melonds_1.1.png"
RAW_OPENING = PACK_ROOT / "png" / "v05_bg" / "V5_000.png"


def load_ui_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = (
        Path(r"C:\Windows\Fonts\malgun.ttf"),
        Path(r"C:\Windows\Fonts\malgunbd.ttf"),
        Path(r"G:\Downloads\Korean Traslate Project\Font\PF스타더스트 3.0 Bold.ttf"),
    )
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    raise FileNotFoundError("No Korean-capable documentation font found")


def centered_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font, fill) -> None:
    box = draw.textbbox((0, 0), text, font=font)
    width = box[2] - box[0]
    height = box[3] - box[1]
    draw.text((xy[0] - width // 2, xy[1] - height // 2 - box[1]), text, font=font, fill=fill)


def source_glyph(sheet: Image.Image, char: str) -> Image.Image:
    index = ord(char) - 0xAC00
    if not 0 <= index < 11172:
        raise ValueError(f"not a precomposed Hangul syllable: {char}")
    x = (index % 64) * 16
    y = (index // 64) * 16
    cell = sheet.crop((x, y, x + 16, y + 16)).convert("RGBA")
    colors = cell.getcolors(maxcolors=256) or []
    transparent = (0, 0, 0, 0)
    background = max(colors, key=lambda item: item[0])[1] if colors else transparent
    mask = Image.new("L", (16, 16), 0)
    pixels = []
    for rgba in cell.get_flattened_data():
        pixels.append(255 if rgba[3] and rgba != background else 0)
    mask.putdata(pixels)
    return mask


def build_font_figure() -> None:
    config = json.loads(FONT_CONFIG.read_text(encoding="utf-8"))
    sheet_path = Path(config["source"]["path"])
    mapping_data = json.loads(MAPPING.read_text(encoding="utf-8"))
    rows = {row["character"]: row for row in mapping_data["mappings"]}
    characters = ["한", "글", "뒤", "로", "묘", "지"]
    missing = [char for char in characters if char not in rows]
    if missing:
        raise ValueError(f"documentation glyphs missing from mapping: {missing}")

    with Image.open(sheet_path) as opened:
        sheet = opened.convert("RGBA")
    canvas = Image.new("RGB", (1200, 430), "#101820")
    draw = ImageDraw.Draw(canvas)
    title_font = load_ui_font(34)
    label_font = load_ui_font(22)
    small_font = load_ui_font(17)
    draw.text((38, 24), "완성형 한글 시트 → 게임용 코드와 16×16 글리프", font=title_font, fill="white")
    draw.text(
        (40, 74),
        "U+AC00부터 순서대로 셀을 찾고, 실제 번역에 필요한 825자만 게임 폰트 칸에 기록했습니다.",
        font=small_font,
        fill="#B9C7D5",
    )

    card_w = 174
    for number, char in enumerate(characters):
        x = 38 + number * 190
        y = 122
        draw.rounded_rectangle((x, y, x + card_w, y + 267), radius=12, fill="#F4F1E8", outline="#607080", width=2)
        glyph = source_glyph(sheet, char).resize((144, 144), Image.Resampling.NEAREST)
        glyph_rgb = Image.new("RGB", glyph.size, "white")
        glyph_rgb.paste("black", mask=glyph)
        canvas.paste(glyph_rgb, (x + 15, y + 15))
        draw.rectangle((x + 15, y + 15, x + 159, y + 159), outline="#A0A0A0", width=1)
        draw.line((x + 87, y + 15, x + 87, y + 159), fill="#FF4C4C", width=2)
        draw.line((x + 15, y + 87, x + 159, y + 87), fill="#FF4C4C", width=2)
        row = rows[char]
        centered_text(draw, (x + 87, y + 190), char, label_font, "#101010")
        centered_text(draw, (x + 87, y + 222), f'코드 {row["code"]}', small_font, "#303840")
        centered_text(draw, (x + 87, y + 248), f'글리프 {row["glyph_index"]}', small_font, "#303840")
    canvas.save(OUTPUT / "02_font_mapping_and_glyphs.png", optimize=False)


def render_final(canvas_id: str) -> Image.Image:
    with Image.open(FINAL_INDEXED / f"{canvas_id}.png") as opened:
        if opened.mode != "P":
            raise ValueError(f"final asset is not indexed: {canvas_id}")
        values = list(opened.get_flattened_data())
        width, height = opened.size
    with render_index_preview(values, width, height) as preview:
        return preview.convert("RGB").resize((width * 4, height * 4), Image.Resampling.NEAREST)


def panel(image: Image.Image, size: tuple[int, int], label: str) -> Image.Image:
    result = Image.new("RGB", size, "#20252A")
    title_font = load_ui_font(25)
    image = image.copy()
    image.thumbnail((size[0] - 24, size[1] - 64), Image.Resampling.NEAREST)
    result.paste(image, ((size[0] - image.width) // 2, 52 + (size[1] - 64 - image.height) // 2))
    draw = ImageDraw.Draw(result)
    centered_text(draw, (size[0] // 2, 27), label, title_font, "white")
    return result


def build_original_previews() -> dict[str, Image.Image]:
    wanted = {"opening_1948", "opening_hide"}
    with GRAPHICS_CONFIG.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        rows = [row for row in reader if row["canvas_id"] in wanted]
    if fieldnames is None or {row["canvas_id"] for row in rows} != wanted:
        raise ValueError("opening documentation rows are missing from v39 config")

    with tempfile.TemporaryDirectory(prefix="another_code_retrospective_") as temporary:
        temp = Path(temporary)
        config = temp / "opening_only.csv"
        with config.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        output = temp / "canvases"
        report = build_edit_canvases(PACK_ROOT, config, output)
        if report["roundtrip_pass_count"] != 2:
            raise AssertionError("original opening canvas roundtrip did not pass")
        result = {}
        for canvas_id in wanted:
            with Image.open(output / "preview_x4" / f"{canvas_id}.png") as opened:
                result[canvas_id] = opened.convert("RGB")
        return result


def build_opening_figures(original: dict[str, Image.Image]) -> None:
    comparison = Image.new("RGB", (1280, 490), "#12171C")
    comparison.paste(panel(original["opening_1948"], (620, 450), "원본: 이미지에 박힌 일본어"), (20, 20))
    comparison.paste(panel(render_final("opening_1948"), (620, 450), "한글판: 같은 인덱스 그래픽을 재조판"), (640, 20))
    comparison.save(OUTPUT / "03_opening_1948_before_after.png", optimize=False)

    ids = [
        ("opening_1948", "연도·장소 카드"),
        ("opening_1994", "1994년 카드"),
        ("opening_2005", "2005년 카드"),
        ("opening_father", "여보…"),
        ("opening_why", "왜…!?"),
        ("opening_hide", "두 줄 오프닝 문구"),
    ]
    montage = Image.new("RGB", (1280, 950), "#12171C")
    for number, (canvas_id, label) in enumerate(ids):
        x = 20 + (number % 3) * 420
        y = 20 + (number // 3) * 465
        montage.paste(panel(render_final(canvas_id), (400, 445), label), (x, y))
    montage.save(OUTPUT / "04_opening_korean_asset_montage.png", optimize=False)

    with Image.open(RAW_OPENING) as opened:
        raw = opened.convert("RGB").resize((512, 384), Image.Resampling.NEAREST)
    palette_figure = Image.new("RGB", (1560, 490), "#12171C")
    palette_figure.paste(panel(raw, (500, 450), "원시 PNG: 단색처럼 보임"), (20, 20))
    palette_figure.paste(panel(original["opening_1948"], (500, 450), "인덱스 강조 + 타일 정렬"), (520, 20))
    palette_figure.paste(panel(render_final("opening_1948"), (500, 450), "팔레트 번호를 보존한 한글"), (1020, 20))
    palette_figure.save(OUTPUT / "05_palette_and_tile_normalization.png", optimize=False)


def copy_runtime_poc() -> None:
    if not RUNTIME_POC.exists():
        raise FileNotFoundError(RUNTIME_POC)
    shutil.copyfile(RUNTIME_POC, OUTPUT / "01_runtime_one_glyph_poc.png")


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    expected = {
        "01_runtime_one_glyph_poc.png",
        "02_font_mapping_and_glyphs.png",
        "03_opening_1948_before_after.png",
        "04_opening_korean_asset_montage.png",
        "05_palette_and_tile_normalization.png",
    }
    unexpected = [path for path in OUTPUT.iterdir() if path.name not in expected and path.name != "00_overview_pipeline.svg"]
    if unexpected:
        raise FileExistsError(f"unexpected files in documentation output: {unexpected}")
    copy_runtime_poc()
    build_font_figure()
    original = build_original_previews()
    build_opening_figures(original)
    missing = sorted(name for name in expected if not (OUTPUT / name).exists())
    if missing:
        raise AssertionError(f"missing documentation figures: {missing}")
    print(json.dumps({"status": "pass", "output": str(OUTPUT), "files": sorted(expected)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
