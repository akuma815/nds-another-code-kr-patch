#!/usr/bin/env python3
"""Build a searchable, pixel-perfect review gallery for extracted graphics."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageDraw


TEXT_NAME = re.compile(
    r"(?:title|logo|start|end|code|panel|window|win|book|card|letter|memo|paper|"
    r"sign|syorui|radio|das|pda|name|message|text)",
    re.IGNORECASE,
)


def image_metrics(path: Path) -> dict[str, float | int]:
    with Image.open(path) as image:
        colors = Counter(image.convert("RGB").get_flattened_data())
    total = sum(colors.values())
    return {
        "unique_colors": len(colors),
        "dominant_fraction": max(colors.values(), default=0) / total if total else 0.0,
    }


def score_candidate(row: dict[str, object], metrics: dict[str, float | int]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    archive = str(row["archive"]).lower()
    name = str(row["resource"]).lower()
    width = int(row["width"])
    height = int(row["height"])
    bpp = int(row["bpp"])
    archive_weights = {"sys_bg": 4, "pda": 4, "mystery": 3, "map_mdl": 1, "item": 1}
    if archive in archive_weights:
        value = archive_weights[archive]
        score += value
        reasons.append(f"archive:{archive}+{value}")
    if TEXT_NAME.search(name):
        score += 4
        reasons.append("text_like_name+4")
    if height <= 64:
        score += 2
        reasons.append("short_height+2")
    if width <= 128:
        score += 1
        reasons.append("small_width+1")
    if bpp == 4:
        score += 1
        reasons.append("16_color+1")
    if int(metrics["unique_colors"]) <= 32:
        score += 1
        reasons.append("limited_colors+1")
    if float(metrics["dominant_fraction"]) >= 0.65:
        score += 1
        reasons.append("dominant_background+1")
    if int(metrics["unique_colors"]) == 1:
        score -= 20
        reasons.append("single_color_blank-20")
    elif float(metrics["dominant_fraction"]) >= 0.995:
        score -= 5
        reasons.append("nearly_blank-5")
    return score, reasons


def priority(score: int) -> str:
    if score >= 8:
        return "high"
    if score >= 5:
        return "medium"
    return "low"


def load_assets(pack_root: Path) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    with (pack_root / "catalog.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if not row["primary_png_path"]:
                continue
            result.append(
                {
                    "asset_id": f"direct:{row['resource_id']}",
                    "asset_type": "direct_2d",
                    "archive": row["archive"],
                    "resource": row["resource"],
                    "model_resource_id": "",
                    "texture_name": "",
                    "width": int(row["width"]),
                    "height": int(row["height"]),
                    "bpp": int(row["bpp"]),
                    "png_path": row["primary_png_path"],
                }
            )
    with (pack_root / "model_texture_catalog.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        for row in csv.DictReader(handle):
            resource = f"{row['model_resource_id'].split('/', 1)[-1]}/{row['texture_name']}"
            result.append(
                {
                    "asset_id": f"model:{row['model_resource_id']}#{int(row['texture_index']):03d}",
                    "asset_type": "model_3d",
                    "archive": row["archive"],
                    "resource": resource,
                    "model_resource_id": row["model_resource_id"],
                    "texture_name": row["texture_name"],
                    "width": int(row["width"]),
                    "height": int(row["height"]),
                    "bpp": int(row["bpp"]),
                    "png_path": row["primary_png_path"],
                }
            )
    return result


def write_candidate_sheets(
    pack_root: Path, output_root: Path, assets: list[dict[str, object]]
) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in assets:
        if row["review_priority"] in ("high", "medium"):
            grouped[str(row["archive"])].append(row)
    output: list[dict[str, object]] = []
    columns, page_size = 2, 20
    cell_width, cell_height = 544, 432
    for archive, rows in sorted(grouped.items()):
        for page_index in range(math.ceil(len(rows) / page_size)):
            page_rows = rows[page_index * page_size : (page_index + 1) * page_size]
            line_count = math.ceil(len(page_rows) / columns)
            sheet = Image.new(
                "RGB", (columns * cell_width, line_count * cell_height), (32, 32, 32)
            )
            draw = ImageDraw.Draw(sheet)
            for index, row in enumerate(page_rows):
                x = (index % columns) * cell_width
                y = (index // columns) * cell_height
                source_path = pack_root / str(row["png_path"])
                with Image.open(source_path) as source:
                    image = source.convert("RGB")
                    scale = max(1, min(8, 512 // image.width, 384 // image.height))
                    if scale > 1:
                        image = image.resize(
                            (image.width * scale, image.height * scale), Image.Resampling.NEAREST
                        )
                sheet.paste(image, (x + (cell_width - image.width) // 2, y + 4))
                label = f"{row['asset_id']}  {row['width']}x{row['height']} {row['bpp']}bpp"
                draw.text((x + 6, y + 394), label, fill=(255, 255, 255))
                draw.text(
                    (x + 6, y + 410),
                    f"priority={row['review_priority']} score={row['candidate_score']}",
                    fill=(255, 220, 96),
                )
            rel = Path("candidate_sheets") / f"{archive}_{page_index + 1:02d}.png"
            target = output_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            sheet.save(target, format="PNG", optimize=False)
            output.append(
                {"archive": archive, "page": page_index + 1, "count": len(page_rows), "path": str(rel)}
            )
    return output


def html_document(assets: list[dict[str, object]], pack_folder: str) -> str:
    payload = json.dumps(assets, ensure_ascii=False).replace("</", "<\\/")
    archives = sorted({str(row["archive"]) for row in assets})
    archive_options = "".join(f'<option value="{value}">{value}</option>' for value in archives)
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>Another Code 그래픽 문자 검수</title>
<style>
body{{margin:0;background:#161616;color:#eee;font:14px system-ui,sans-serif}}header{{position:sticky;top:0;z-index:2;background:#242424;padding:10px;box-shadow:0 2px 8px #000}}
.controls{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}input,select,button{{font:inherit;padding:5px}}#grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:10px;padding:10px}}
.card{{background:#252525;border:1px solid #444;padding:8px;overflow:auto;min-height:220px}}.viewport{{min-height:150px;display:flex;align-items:flex-start;justify-content:center;background-color:#aaa;background-image:linear-gradient(45deg,#888 25%,transparent 25%),linear-gradient(-45deg,#888 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#888 75%),linear-gradient(-45deg,transparent 75%,#888 75%);background-size:16px 16px;background-position:0 0,0 8px,8px -8px,-8px 0}}
.viewport img{{image-rendering:pixelated;image-rendering:crisp-edges;max-width:none}}.id{{font-weight:700;color:#ffe071;word-break:break-all}}.meta{{color:#bbb;margin-top:5px}}.reason{{font-size:12px;color:#8fd0ff;word-break:break-all}}#status{{margin-left:auto}}.pager{{display:flex;gap:6px;align-items:center}}
</style></head><body><header><div class="controls">
<strong>Another Code 그래픽 문자 검수</strong><input id="search" placeholder="자원명 검색"><select id="archive"><option value="">전체 archive</option>{archive_options}</select>
<select id="priority"><option value="high,medium">우선 후보</option><option value="high">high만</option><option value="high,medium,low">전체</option></select>
<select id="type"><option value="">2D+3D</option><option value="direct_2d">2D</option><option value="model_3d">3D</option></select>
<label>확대 <input id="zoom" type="range" min="1" max="8" value="4"><span id="zoomValue">4×</span></label>
<button id="prev">이전</button><button id="next">다음</button><span id="status"></span></div></header><main id="grid"></main>
<script>const assets={payload};const pageSize=40;let page=0;const q=id=>document.getElementById(id);function filtered(){{const term=q('search').value.toLowerCase();const ps=q('priority').value.split(',');return assets.filter(a=>(!term||(a.asset_id+' '+a.resource).toLowerCase().includes(term))&&(!q('archive').value||a.archive===q('archive').value)&&(!q('type').value||a.asset_type===q('type').value)&&ps.includes(a.review_priority));}}
function render(){{const rows=filtered();const pages=Math.max(1,Math.ceil(rows.length/pageSize));page=Math.min(page,pages-1);const zoom=Number(q('zoom').value);q('zoomValue').textContent=zoom+'×';q('status').textContent=`${{rows.length}}개 / ${{page+1}}·${{pages}} 페이지`;q('grid').innerHTML='';for(const a of rows.slice(page*pageSize,(page+1)*pageSize)){{const card=document.createElement('article');card.className='card';const src='../{pack_folder}/'+a.png_path.replaceAll('\\\\','/');card.innerHTML=`<div class="id">${{a.asset_id}}</div><div class="viewport"><img loading="lazy" src="${{src}}" width="${{a.width*zoom}}" height="${{a.height*zoom}}"></div><div class="meta">${{a.width}}×${{a.height}} · ${{a.bpp}}bpp · ${{a.asset_type}} · ${{a.review_priority}}(${{a.candidate_score}})</div><div class="reason">${{a.candidate_reasons}}</div>`;q('grid').appendChild(card);}}}}
for(const id of ['search','archive','priority','type','zoom'])q(id).addEventListener('input',()=>{{page=0;render();}});q('prev').onclick=()=>{{page=Math.max(0,page-1);render();scrollTo(0,0)}};q('next').onclick=()=>{{page++;render();scrollTo(0,0)}};render();</script></body></html>"""


def build(pack_root: Path, output_root: Path) -> dict[str, object]:
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"refusing to overwrite nonempty review root: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    assets = load_assets(pack_root)
    counts: Counter[str] = Counter()
    for row in assets:
        metrics = image_metrics(pack_root / str(row["png_path"]))
        score, reasons = score_candidate(row, metrics)
        row.update(metrics)
        row["candidate_score"] = score
        row["candidate_reasons"] = ";".join(reasons)
        row["review_priority"] = priority(score)
        row["review_state"] = "unreviewed"
        row["source_text"] = ""
        row["translation_text"] = ""
        row["notes"] = ""
        counts[str(row["review_priority"])] += 1
    assets.sort(key=lambda row: (-int(row["candidate_score"]), str(row["asset_id"])))
    fields = tuple(assets[0].keys())
    csv_path = output_root / "graphics_text_review.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(assets)
    sheets = write_candidate_sheets(pack_root, output_root, assets)
    html_path = output_root / "index.html"
    html_path.write_text(html_document(assets, pack_root.name), encoding="utf-8")
    report = {
        "schema_version": 1,
        "status": "review_gallery_ready_candidate_scores_are_not_text_confirmation",
        "pack_root": str(pack_root),
        "asset_count": len(assets),
        "priority_counts": dict(sorted(counts.items())),
        "candidate_sheet_count": len(sheets),
        "candidate_sheets": sheets,
        "outputs": {"html": "index.html", "csv": "graphics_text_review.csv"},
        "not_proved": [
            "Japanese text presence in any asset until human review",
            "completeness of the high and medium heuristic candidate set",
            "graphics reinsertion or runtime consumption",
        ],
    }
    (output_root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    build(args.pack_root, args.output_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
