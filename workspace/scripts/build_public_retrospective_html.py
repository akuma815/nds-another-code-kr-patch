#!/usr/bin/env python3
"""Build a public, self-contained HTML retrospective with embedded figures."""

from __future__ import annotations

import base64
import hashlib
from html.parser import HTMLParser
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from PIL import Image


PROJECT = Path(__file__).resolve().parents[2]
ASSETS = PROJECT / "docs" / "assets" / "font_opening_retrospective"
OUTPUT = PROJECT / "docs" / "public" / "another_code_font_opening_retrospective.html"
FRAGMENT_OUTPUT = PROJECT / "docs" / "public" / "another_code_font_opening_retrospective_board_fragment.html"
SHARE_ROOT = PROJECT / "docs" / "public" / "another_code_font_opening_share"
SHARE_HTML = SHARE_ROOT / "index.html"
SHARE_ZIP = PROJECT / "docs" / "public" / "another_code_font_opening_share.zip"
TOOL_FILES = (
    "README_KO.md",
    "requirements.txt",
    "LICENSE.txt",
    "example_mapping.json",
    "01_extract_hangul_demand.py",
    "02_analyze_sjis_font.py",
    "03_audit_hangul_mapping.py",
    "04_build_hangul_font.py",
    "05_mapping_to_tbl.py",
)

FIGURES = {
    "overview": ("00_overview_pipeline.svg", "image/svg+xml"),
    "runtime": ("01_runtime_one_glyph_poc.png", "image/png"),
    "glyphs": ("02_font_mapping_and_glyphs.png", "image/png"),
    "before_after": ("03_opening_1948_before_after.png", "image/png"),
    "montage": ("04_opening_korean_asset_montage.png", "image/png"),
    "palette": ("05_palette_and_tile_normalization.png", "image/png"),
}


def data_uri(filename: str, mime: str) -> str:
    data = (ASSETS / filename).read_bytes()
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def image_size(filename: str) -> tuple[int, int]:
    if filename.lower().endswith(".svg"):
        return 1200, 560
    with Image.open(ASSETS / filename) as image:
        return image.size


class HtmlAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            values = dict(attrs)
            self.images.append(values.get("src") or "")


def build() -> str:
    embedded = {name: data_uri(*spec) for name, spec in FIGURES.items()}
    sizes = {name: image_size(spec[0]) for name, spec in FIGURES.items()}
    html = f'''<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>《어나더 코드: 두 개의 기억》 한글 폰트와 오프닝 글씨 작업 후기</title>
  <style>
    :root {{ color-scheme: light; --bg:#f4f5f7; --paper:#fff; --ink:#1a2027; --muted:#596775; --line:#dce1e6; --accent:#b04a22; --code:#17212b; --note:#fff5dc; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font-family:"Noto Sans KR","Apple SD Gothic Neo","Malgun Gothic",sans-serif; line-height:1.78; word-break:keep-all; }}
    article {{ width:min(920px, calc(100% - 28px)); margin:28px auto; padding:clamp(24px, 6vw, 68px); background:var(--paper); border:1px solid var(--line); border-radius:18px; box-shadow:0 8px 30px rgba(20,30,40,.08); }}
    h1 {{ margin:0 0 12px; font-size:clamp(2rem,5vw,3.1rem); line-height:1.23; letter-spacing:-.045em; }}
    h2 {{ margin:3.1em 0 .8em; padding-bottom:.35em; border-bottom:2px solid var(--line); font-size:1.55rem; line-height:1.4; letter-spacing:-.025em; }}
    h3 {{ margin:2em 0 .5em; font-size:1.15rem; }}
    p {{ margin:.85em 0; }}
    .lead {{ color:#34404a; font-size:1.1rem; }}
    .meta {{ color:var(--muted); font-size:.92rem; }}
    .notice {{ margin:1.5em 0; padding:18px 20px; background:var(--note); border-left:5px solid #e6a51a; border-radius:6px; }}
    nav {{ margin:2em 0; padding:18px 24px; background:#f7f8fa; border:1px solid var(--line); border-radius:12px; }}
    nav strong {{ display:block; margin-bottom:7px; }}
    nav ol {{ margin:.2em 0; padding-left:1.4em; columns:2; column-gap:34px; }}
    nav a {{ color:#40596e; text-decoration:none; }}
    nav a:hover {{ text-decoration:underline; }}
    figure {{ margin:2em 0; }}
    figure img {{ display:block; width:100%; height:auto; border:1px solid #cbd2d8; border-radius:10px; background:#101820; }}
    figcaption {{ margin-top:.65em; color:var(--muted); font-size:.92rem; line-height:1.55; }}
    table {{ width:100%; margin:1.4em 0; border-collapse:collapse; font-size:.96rem; }}
    th, td {{ padding:10px 12px; border:1px solid var(--line); text-align:left; vertical-align:top; }}
    th {{ background:#f1f3f5; }}
    code {{ padding:.12em .35em; border-radius:4px; background:#edf0f2; font-family:Consolas,"D2Coding",monospace; font-size:.92em; word-break:break-all; }}
    pre {{ overflow:auto; padding:18px; background:var(--code); color:#eef5fa; border-radius:10px; line-height:1.55; tab-size:4; }}
    pre code {{ padding:0; background:transparent; color:inherit; }}
    blockquote {{ margin:1.5em 0; padding:4px 0 4px 20px; border-left:4px solid #8096a8; color:#43515d; }}
    .flow {{ padding:18px 22px; background:#f7f8fa; border:1px solid var(--line); border-radius:10px; font-family:Consolas,"D2Coding",monospace; white-space:pre-line; }}
    .closing {{ margin-top:3em; padding-top:1.3em; border-top:1px solid var(--line); color:var(--muted); }}
    @media (max-width:640px) {{ article {{ width:100%; margin:0; border:0; border-radius:0; }} nav ol {{ columns:1; }} body {{ word-break:normal; }} }}
  </style>
</head>
<body>
<article>
  <header>
    <h1>《어나더 코드: 두 개의 기억》<br>한글 폰트와 오프닝 글씨 작업 후기</h1>
    <p class="lead">“한글 문자표와 폰트는 어떻게 만들었나요? 영상처럼 나오는 장면의 글씨는 어떻게 바꿨나요?”라는 질문을 받아, 실제 작업에서 확인한 구조와 시행착오를 정리해 봤습니다.</p>
    <p class="meta">대상 플랫폼: Nintendo DS · 내용: 문자 인코딩, 2bpp 폰트, 문자열 재배치, 압축 그래픽 한글화</p>
  </header>

  <div class="notice"><strong>먼저 바로잡을 점</strong><br>이 작업에서는 흔히 말하는 <code>korean.tbl</code> 형식의 문자표를 최종 원본으로 쓰지 않았습니다. 같은 역할을 하되 코드, 글리프 번호, 충돌 조사 결과까지 함께 기록할 수 있는 구조화된 매핑 데이터를 사용했습니다. 편의상 이 글에서는 이를 “한글 문자표”라고 부르겠습니다.</div>

  <nav aria-label="목차">
    <strong>목차</strong>
    <ol>
      <li><a href="#structure">전체 구조</a></li>
      <li><a href="#font-format">폰트 형식 분석</a></li>
      <li><a href="#mapping">한글 825자 배정</a></li>
      <li><a href="#font-build">한글 도트 제작</a></li>
      <li><a href="#poc">한 글자 PoC</a></li>
      <li><a href="#dual-font">두 폰트의 함정</a></li>
      <li><a href="#text">문자열 인코딩·재배치</a></li>
      <li><a href="#opening">오프닝 글씨의 정체</a></li>
      <li><a href="#graphics">압축 그래픽 처리</a></li>
      <li><a href="#palette">타일과 팔레트</a></li>
      <li><a href="#tools">공유 Python 도구</a></li>
      <li><a href="#verify">검증 방법</a></li>
      <li><a href="#lessons">작업 교훈</a></li>
    </ol>
  </nav>

  <section id="structure">
    <h2>1. 한글 한 글자가 화면에 나오기까지</h2>
    <p>한글 폰트 작업은 글자 그림만 교체하는 일이 아니었습니다. 번역문 속 한글을 게임이 이해하는 코드로 바꾸고, 그 코드가 정확한 글리프 칸을 가리키게 한 뒤, 길어진 문장을 다시 배치해야 했습니다.</p>
    <div class="flow">번역문에서 사용 한글 집계
↓
각 한글에 게임용 2바이트 코드 배정
↓
코드가 가리키는 16×16 글리프 칸에 한글 도트 삽입
↓
번역문을 새 코드로 인코딩
↓
문자열 재배치와 포인터 갱신
↓
게임의 기존 출력기가 한글을 표시</div>
    <figure>
      <img src="{embedded['overview']}" width="{sizes['overview'][0]}" height="{sizes['overview'][1]}" alt="실시간 본문 폰트와 이미지에 박힌 오프닝 글씨의 서로 다른 처리 흐름">
      <figcaption>실시간 본문은 코드·글리프 매핑을 사용하지만, 이미지에 박힌 오프닝 글씨는 압축 그래픽을 별도로 해제하고 재조판합니다.</figcaption>
    </figure>
  </section>

  <section id="font-format">
    <h2>2. 원본 폰트 형식을 어떻게 알아냈나</h2>
    <p>게임 안에는 크기가 같은 본문 폰트가 두 종류 있었고, 각 파일은 280,000바이트였습니다. 파일 크기, 반복 패턴, 실제 출력 결과를 대조해 다음 구조를 확정했습니다.</p>
    <table>
      <thead><tr><th>항목</th><th>확인한 값</th></tr></thead>
      <tbody>
        <tr><td>글리프 크기</td><td>16×16픽셀</td></tr>
        <tr><td>색 깊이</td><td>2bpp, 픽셀값 0~3</td></tr>
        <tr><td>글리프당 크기</td><td>64바이트</td></tr>
        <tr><td>총 글리프</td><td>4,375개</td></tr>
        <tr><td>내부 구성</td><td>8×8 타일 네 장</td></tr>
        <tr><td>타일 순서</td><td>좌상 → 우상 → 좌하 → 우하</td></tr>
      </tbody>
    </table>
    <p><code>16×16×2비트 = 512비트 = 64바이트</code>이며, 전체 파일도 <code>280000÷64=4375</code>로 정확히 나누어집니다. 한 바이트에는 2비트 픽셀 네 개가 상위 비트부터 차례대로 들어갑니다.</p>
  </section>

  <section id="mapping">
    <h2>3. 게임용 코드와 글리프 번호의 관계</h2>
    <p>본문은 Shift-JIS와 비슷한 2바이트 코드를 사용했습니다. 선행 바이트와 후행 바이트를 그대로 유니코드 문자로 해석하는 것이 아니라, 게임의 계산식으로 글리프 번호를 구해야 했습니다.</p>
    <pre><code>선행 바이트 하나당 글리프 188개
후행 바이트 범위: 0x40~0xFC
단, 0x7F는 건너뜀

글리프 번호 = 선행 바이트 번호 × 188 + 후행 바이트 번호</code></pre>
    <p>예를 들어 어떤 한글에 <code>0x889E</code>를 배정하고 계산 결과가 1409라면, 폰트 시작점에서 <code>1409×64</code>바이트 떨어진 곳에 그 한글 그림을 기록합니다. 번역 문자열에도 반드시 같은 2바이트 코드를 써야 합니다.</p>

    <h3>완성형 11,172자를 전부 넣지 않은 이유</h3>
    <p>원본 폰트는 4,375칸뿐이고 일본어·숫자·기호도 보존해야 했습니다. 그래서 최종 번역문을 먼저 검사해 실제로 쓰이는 한글 825자만 추렸습니다.</p>
    <p>원본에서 완전히 빈 글리프 630개를 우선 사용했지만 그것만으로는 부족했습니다. 나머지 195칸은 원본 ROM, 추출 스크립트, 압축 리소스, 비한글 문자 사용처를 교차 조사해 재사용 후보를 골랐습니다. “CP932에서 정의되지 않는다”거나 “폰트 그림이 비어 있다”는 이유 하나만으로 안전하다고 판단하지 않았습니다.</p>
  </section>

  <section id="font-build">
    <h2>4. 한글 도트 폰트 제작</h2>
    <p>최종 본문 글꼴은 16×16 셀에 들어 있는 Galmuri11 Condensed 12px 계열을 선택했습니다. 시트에는 <code>가</code>부터 <code>힣</code>까지 완성형 한글 11,172자가 유니코드 순서로 들어 있었습니다.</p>
    <pre><code>셀 번호 = 한글 유니코드값 - 0xAC00
x = (셀 번호 % 64) × 16
y = (셀 번호 // 64) × 16</code></pre>
    <p>각 셀에서 배경은 0, 글자 획은 3으로 바꾼 뒤, 16×16 픽셀을 8×8 타일 네 장으로 나누어 64바이트로 패킹했습니다.</p>
    <figure>
      <img src="{embedded['glyphs']}" width="{sizes['glyphs'][0]}" height="{sizes['glyphs'][1]}" alt="한글 여섯 글자의 16×16 도트, 게임용 코드, 글리프 번호 예시">
      <figcaption>빨간 선은 8×8 타일 네 장의 경계입니다. 유니코드 한글을 ROM에 직접 넣는 것이 아니라, 각 글자에 배정된 게임용 코드와 글리프 번호를 사용했습니다.</figcaption>
    </figure>
    <p>작은 글꼴을 단순히 2배 확대하는 실험과 10px·12px 후보 비교도 거쳤습니다. PC 미리보기에서 선명한 것과 NDS 실제 해상도에서 읽기 좋은 것은 달랐고, 최종 결정은 에뮬레이터 화면에서 내렸습니다.</p>
  </section>

  <section id="poc">
    <h2>5. 처음에는 한 글자만 바꿨다</h2>
    <p>처음부터 825자를 전부 넣으면 문제가 생겨도 코드, 폰트, 문자열 중 어디가 틀렸는지 알기 어렵습니다. 첫 시험에서는 일본어 문장의 첫 글자 코드만 바꾸고 그 칸에 <code>한</code> 글리프 하나를 넣었습니다.</p>
    <figure>
      <img src="{embedded['runtime']}" width="{sizes['runtime'][0]}" height="{sizes['runtime'][1]}" alt="melonDS에서 문장 첫 글자만 한글 한으로 표시된 실행 화면">
      <figcaption>실제 melonDS PoC 화면입니다. 첫 글자만 한글로 바꾸고 뒤 일본어는 남겨, 한글 표시와 인접 일본어 보존을 한 번에 확인했습니다.</figcaption>
    </figure>
    <p>이때 바뀐 범위는 문자열 코드 2바이트와 글리프 64바이트뿐이었습니다. 허용한 66바이트 밖이 하나라도 달라지면 빌드를 중단하도록 했습니다. 이후 실제 오프닝 첫 문장, 여러 화면, 선행 바이트 대표 코드, 전체 번역 순으로 범위를 넓혔습니다.</p>
  </section>

  <section id="dual-font">
    <h2>6. 한 장면에서는 되는데 뒤에서 깨졌던 이유</h2>
    <p>초기 화면은 한쪽 본문 폰트만 수정해도 정상적으로 보였습니다. 하지만 게임은 제어 명령에 따라 크기가 같은 두 폰트를 전환하고 있었습니다. 한쪽만 한글화하면 특정 장면부터 원래 글리프가 나타나거나 깨집니다.</p>
    <p>최종 빌드에서는 같은 825개 한글 글리프를 두 폰트에 모두 넣었습니다. 이 경험 때문에 “첫 화면에서 보였다”와 “게임 전체 출력 경로가 해결됐다”를 별개의 검증으로 보게 됐습니다.</p>
  </section>

  <section id="text">
    <h2>7. 번역문 인코딩과 긴 문장 재배치</h2>
    <p>번역문의 한글은 문자표의 2바이트 코드로 바꾸고, 숫자와 기존 기호는 원래 인코딩을 유지했습니다. 줄바꿈, 대기, 화자, 폰트 전환에 쓰이는 제어 명령은 일반 글자로 처리하지 않고 원문과 동일하게 보존했습니다.</p>
    <p>전체 텍스트 카탈로그는 2,789행이었고, 승인된 번역은 2,748행이었습니다. 최종 검사에서는 제어 명령 불일치, 잘못된 명령 표기, 지원되지 않는 유니코드가 모두 0건이었습니다.</p>
    <p>한국어는 원문보다 길어지는 경우가 많아 개별 고정 칸에 그대로 덮을 수 없었습니다. 연결된 문자열 묶음을 같은 데이터 구역 안에서 다시 배치하고, 각 문자열을 가리키는 32비트 포인터를 갱신했습니다. 4바이트 정렬과 영역 용량을 지켰으며 ROM 크기는 늘리지 않았습니다.</p>
  </section>

  <section id="opening">
    <h2>8. “동영상 글씨”는 실제 동영상 자막이 아니었다</h2>
    <p>화면에서는 영상처럼 재생되지만, 문제가 된 오프닝 문구 대부분은 동영상 프레임에 박힌 자막이 아니었습니다. 여러 장의 압축 그래픽을 순서대로 불러와 효과를 합성하는 구조였습니다.</p>
    <table>
      <thead><tr><th>화면의 글씨 종류</th><th>처리 방법</th></tr></thead>
      <tbody>
        <tr><td>엔진이 실시간으로 그리는 도입 대사</td><td>앞에서 설명한 한글 문자표와 본문 폰트 사용</td></tr>
        <tr><td>연도·장소 카드와 짧은 오프닝 문구</td><td>이미지에 박힌 일본어를 지우고 한글 그래픽으로 재조판</td></tr>
      </tbody>
    </table>
    <p>그래픽 내용만 바꾸고 리소스의 재생 순서와 전환 코드는 건드리지 않았기 때문에 장면의 타이밍은 그대로 유지됐습니다.</p>
    <figure>
      <img src="{embedded['before_after']}" width="{sizes['before_after'][0]}" height="{sizes['before_after'][1]}" alt="1948년 오프닝 카드의 일본어 원본과 한글판 비교">
      <figcaption>왼쪽은 원본 픽셀 인덱스를 강조한 편집용 화면, 오른쪽은 같은 그래픽을 한글로 다시 조판한 결과입니다.</figcaption>
    </figure>
    <figure>
      <img src="{embedded['montage']}" width="{sizes['montage'][0]}" height="{sizes['montage'][1]}" alt="연도 카드와 대사 등 한글로 다시 만든 오프닝 그래픽 여섯 장">
      <figcaption>연도·장소 카드와 짧은 대사 이미지의 예입니다. 검사용 인덱스 미리보기이므로 실제 장면의 색과는 다릅니다. 오른쪽 아래의 가는 반복 띠는 편집 영역 밖 타일 스트림의 랩 구간입니다.</figcaption>
    </figure>
  </section>

  <section id="graphics">
    <h2>9. 압축 그래픽을 꺼내고 다시 넣은 방법</h2>
    <p>게임의 그래픽 묶음은 일반 이미지 폴더가 아니라 독자 아카이브였습니다. 조사 결과 47개 아카이브 안에 1,319개 리소스가 있었고, 그중 1,147개가 압축돼 있었습니다.</p>
    <p>ARM9의 해제 루틴을 분석해 64KiB 링 버퍼를 쓰는 LZSS 계열 방식을 재현했습니다. 해제 후에는 직접 인덱스 이미지 1,029개와 선형 3D 텍스처 384개, 합계 1,413개의 검토용 PNG를 만들 수 있었습니다.</p>
    <div class="flow">압축 리소스 해제
↓
4bpp/8bpp 픽셀 인덱스 추출
↓
8×8 타일 순서를 편집 화면으로 정규화
↓
일본어 영역 제거와 한글 조판
↓
원래 타일 순서로 역변환
↓
재압축·아카이브 재구성·ROM 삽입</div>
  </section>

  <section id="palette">
    <h2>10. 단색 PNG와 8×8 타일의 함정</h2>
    <p>일부 그래픽은 저장된 팔레트만 보면 한 색뿐이어서 일반 PNG로 열면 단색 화면처럼 보였습니다. 실제 색상표는 실행 중에 다른 곳에서 공급됐지만 픽셀 인덱스 자체는 남아 있었습니다. 그래서 RGB 색 대신 인덱스 값의 차이를 강조하는 미리보기를 만들어 일본어 글자 모양을 찾았습니다.</p>
    <figure>
      <img src="{embedded['palette']}" width="{sizes['palette'][0]}" height="{sizes['palette'][1]}" alt="단색 원시 PNG, 픽셀 인덱스를 강조한 일본어 원본, 팔레트 번호를 보존한 한글판 비교">
      <figcaption>단색으로 보이는 원시 PNG도 인덱스 차이를 강조하고 타일 시작점을 맞추면 일본어가 드러납니다. 마지막 단계에서는 같은 인덱스 체계를 유지하며 한글을 넣었습니다.</figcaption>
    </figure>
    <p>오프닝의 여러 이미지는 화면 좌표와 저장 타일 스트림의 시작점이 8타일, 즉 64픽셀 어긋나 있었습니다. 픽셀 행을 단순 이동하자 타일 경계 뒤 내용이 8픽셀 아래로 밀렸고, 전체 8×8 타일 스트림을 순환시키는 방식으로 고쳤습니다.</p>

    <h3>색을 직접 칠하지 않고 팔레트 번호를 기록</h3>
    <p>최종 베이크드 그래픽은 PF Stardust Bold 계열로 조판했습니다. 글자를 1비트 마스크로 만들고 1픽셀 외곽선을 생성한 뒤, 원본 이미지에 실제 존재하는 배경·외곽선·본문 팔레트 번호만 사용했습니다.</p>
    <p>초기에는 화면상 비슷해 보이는 새 인덱스를 사용했다가 한글 획 일부가 검거나 회색 블록으로 변했습니다. 이후에는 사용할 모든 인덱스가 원본 자산에 실제로 존재하는지 검사하도록 빌드를 강화했습니다.</p>
  </section>

  <section id="tools">
    <h2>11. 분석용 Python 도구도 함께 공유합니다</h2>
    <p>글만으로는 실제 구조를 확인하기 어려워, 이 글의 첨부 ZIP에 범용 예제 스크립트 다섯 개와 한국어 설명서를 함께 넣었습니다. 원본 ROM, 게임 데이터, 번역문, 폰트 파일은 포함하지 않았습니다.</p>
    <table>
      <thead><tr><th>첨부 파일</th><th>할 수 있는 일</th></tr></thead>
      <tbody>
        <tr><td><code>01_extract_hangul_demand.py</code></td><td>UTF-8 텍스트·CSV·JSON에서 실제 사용 한글과 등장 빈도를 추출합니다.</td></tr>
        <tr><td><code>02_analyze_sjis_font.py</code></td><td>16×16 2bpp 폰트의 글리프 수와 빈 칸을 분석하고 전체 아틀라스 PNG를 만듭니다.</td></tr>
        <tr><td><code>03_audit_hangul_mapping.py</code></td><td>한글·2바이트 코드·글리프 번호의 중복, 범위, 계산식 불일치를 검사합니다.</td></tr>
        <tr><td><code>04_build_hangul_font.py</code></td><td>완성형 한글 시트에서 필요한 글자를 꺼내 2bpp 글리프로 패킹합니다.</td></tr>
        <tr><td><code>05_mapping_to_tbl.py</code></td><td>검사된 JSON 매핑을 일반적인 <code>XXXX=한</code> 형식의 <code>.tbl</code>로 변환합니다.</td></tr>
      </tbody>
    </table>
    <p>각 도구의 명령 예와 입력 형식은 첨부된 <code>README_KO.md</code>에 정리했습니다. PNG 처리에는 Python 3.10 이상과 Pillow가 필요합니다.</p>
    <pre><code>python -m pip install -r requirements.txt
python 01_extract_hangul_demand.py translated.txt --output-json demand.json
python 02_analyze_sjis_font.py font.bin --report report.json --atlas atlas.png
python 03_audit_hangul_mapping.py mapping.json --font font.bin --report audit.json
python 05_mapping_to_tbl.py mapping.json korean.tbl</code></pre>
    <div class="notice"><strong>주의</strong><br>도구는 다른 게임에서 안전한 코드 영역을 자동으로 정해 주지 않습니다. “빈 글리프”, “원문에 안 나오는 코드”, “CP932 미정의 코드”는 후보일 뿐입니다. 대상 게임의 ROM·스크립트·압축 리소스·제어 코드·실제 렌더러를 조사한 뒤, 반드시 한 글자 PoC부터 검증해야 합니다.</div>
  </section>

  <section id="verify">
    <h2>12. 무엇을 검증했나</h2>
    <h3>텍스트와 폰트</h3>
    <ul>
      <li>한글 825자의 코드와 글리프가 일대일로 대응하는지</li>
      <li>두 본문 폰트에 같은 글리프가 들어갔는지</li>
      <li>제어 명령, 종단 바이트, 포인터가 보존되는지</li>
      <li>재배치된 문자열이 영역을 넘거나 겹치지 않는지</li>
      <li>독립적으로 두 번 빌드한 ROM이 바이트 단위로 같은지</li>
    </ul>
    <h3>그래픽</h3>
    <ul>
      <li>수정 영역 밖의 픽셀이 바뀌지 않았는지</li>
      <li>원본 헤더와 팔레트 데이터가 보존되는지</li>
      <li>재압축한 자산을 다시 풀었을 때 픽셀 인덱스가 일치하는지</li>
      <li>아카이브 정렬, 리소스 기술자, NDS 파일 경계가 정상인지</li>
      <li>최종 누적 그래픽 65개가 ROM에서 다시 읽히는지</li>
    </ul>
    <p>정적 검증 뒤에는 새 ROM을 콜드 부팅하고 일반 저장 파일로 진행했습니다. 세이브스테이트는 이전 ROM의 RAM·VRAM 상태를 그대로 품을 수 있어 새 빌드 검증에는 사용하지 않았습니다.</p>
  </section>

  <section id="lessons">
    <h2>13. 작업하면서 얻은 교훈</h2>
    <ol>
      <li><strong>문자표는 출발점일 뿐입니다.</strong> 코드가 렌더러에서 허용되고 정확한 글리프를 가리키며 다른 데이터와 충돌하지 않아야 합니다.</li>
      <li><strong>“빈 코드”는 한 가지 검색으로 확정하면 안 됩니다.</strong> 스크립트, ROM 전체, 압축 리소스와 런타임을 함께 봐야 합니다.</li>
      <li><strong>한 화면의 성공은 전체 게임의 성공이 아닙니다.</strong> 폰트 전환과 다른 렌더링 경로가 숨어 있을 수 있습니다.</li>
      <li><strong>영상처럼 보여도 먼저 자산 구조를 확인해야 합니다.</strong> 이번 오프닝은 동영상 재인코딩보다 압축 그래픽 교체가 정답이었습니다.</li>
      <li><strong>인덱스 이미지는 색보다 번호가 중요합니다.</strong> RGB 변환 후 저장하면 팔레트 순서가 바뀌어 게임에서 전혀 다른 색이 나올 수 있습니다.</li>
      <li><strong>미리보기와 실행 화면은 별도 관문입니다.</strong> 런타임 팔레트, 뒤집기, 2D·3D 합성, VRAM 상태까지 확인해야 합니다.</li>
    </ol>
    <blockquote>결국 가장 빠른 방법은 처음부터 크게 만드는 것이 아니라, 한 글자·한 문장·한 이미지처럼 원인을 구분할 수 있는 작은 PoC부터 성공시키고 검증 범위를 넓히는 것이었습니다.</blockquote>
  </section>

  <p class="closing">이 글은 한글 패치 제작 과정의 기술적 기록을 목적으로 작성했습니다. 원본 게임 ROM이나 원본 데이터는 포함하지 않습니다.</p>
</article>
</body>
</html>
'''
    return html


def audit(html: str) -> dict[str, object]:
    forbidden = (
        "G:\\",
        "G:/",
        "workspace/",
        "workspace\\",
        "docs/",
        "docs\\",
        "V5_",
        "202608",
        "hangul_mapping_full",
        "graphics_edit_transforms",
        "translate_final_v",
        "build_public_retrospective",
        "build_retrospective_figures",
    )
    leaked = [token for token in forbidden if token in html]
    if leaked:
        raise ValueError(f"local/internal filename tokens leaked into public HTML: {leaked}")

    parser = HtmlAudit()
    parser.feed(html)
    if len(parser.images) != len(FIGURES):
        raise ValueError(f"expected {len(FIGURES)} embedded images, found {len(parser.images)}")
    decoded = []
    expected_hashes = {
        hashlib.sha256((ASSETS / filename).read_bytes()).hexdigest()
        for filename, _mime in FIGURES.values()
    }
    decoded_hashes = set()
    for source in parser.images:
        if not source.startswith("data:image/") or ";base64," not in source:
            raise ValueError("non-embedded image found")
        header, payload = source.split(",", 1)
        data = base64.b64decode(payload, validate=True)
        if len(data) < 100:
            raise ValueError(f"embedded image is unexpectedly small: {header}")
        decoded_hashes.add(hashlib.sha256(data).hexdigest())
        decoded.append({"mime": header[5:].split(";", 1)[0], "bytes": len(data)})
    if decoded_hashes != expected_hashes:
        raise ValueError("embedded image bytes do not match the approved documentation figures")
    return {"image_count": len(decoded), "images": decoded}


def board_fragment(html: str) -> str:
    style = html.split("<style>", 1)[1].split("</style>", 1)[0]
    article = html.split("<article>", 1)[1].split("</article>", 1)[0]
    return f'<style>{style}</style>\n<article>\n{article}\n</article>\n'


def build_share_zip() -> dict[str, object]:
    expected = [SHARE_HTML, *[SHARE_ROOT / "tools" / name for name in TOOL_FILES]]
    missing = [str(path) for path in expected if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"share package inputs missing: {missing}")
    if SHARE_ZIP.exists():
        SHARE_ZIP.unlink()
    with ZipFile(SHARE_ZIP, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in expected:
            relative = path.relative_to(SHARE_ROOT).as_posix()
            info = ZipInfo(relative, date_time=(2026, 9, 2, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compress_type=ZIP_DEFLATED, compresslevel=9)
    return {
        "file_count": len(expected),
        "size": SHARE_ZIP.stat().st_size,
        "sha256": hashlib.sha256(SHARE_ZIP.read_bytes()).hexdigest().upper(),
    }


def main() -> int:
    html = build()
    report = audit(html)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html, encoding="utf-8", newline="\n")
    fragment = board_fragment(html)
    fragment_report = audit(fragment)
    FRAGMENT_OUTPUT.write_text(fragment, encoding="utf-8", newline="\n")
    SHARE_ROOT.mkdir(parents=True, exist_ok=True)
    SHARE_HTML.write_text(html, encoding="utf-8", newline="\n")
    package = build_share_zip()
    encoded = OUTPUT.read_bytes()
    fragment_encoded = FRAGMENT_OUTPUT.read_bytes()
    print(f"output={OUTPUT}")
    print(f"size={len(encoded)}")
    print(f"sha256={hashlib.sha256(encoded).hexdigest().upper()}")
    print(f"embedded_images={report['image_count']}")
    print(f"fragment_output={FRAGMENT_OUTPUT}")
    print(f"fragment_size={len(fragment_encoded)}")
    print(f"fragment_sha256={hashlib.sha256(fragment_encoded).hexdigest().upper()}")
    print(f"fragment_embedded_images={fragment_report['image_count']}")
    print(f"share_html={SHARE_HTML}")
    print(f"share_zip={SHARE_ZIP}")
    print(f"share_zip_files={package['file_count']}")
    print(f"share_zip_size={package['size']}")
    print(f"share_zip_sha256={package['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
