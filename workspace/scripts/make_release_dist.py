"""Create the ROM-free Another Code Korean patch distribution."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]
RELEASE_DATE = "20260829"
RELEASE_LABEL = "Final"
RELEASE_NAME = f"AnotherCode_KR_Patch_{RELEASE_LABEL}_{RELEASE_DATE}"
SOURCE_ROM = PROJECT / "original" / "Another Code 2tsu no Kioku.nds"
TARGET_ROM = PROJECT / "build" / "another_code_ko_final_20260829.nds"
TRANSLATION_CSV = (
    PROJECT / "workspace" / "text" / "translation" / "another_code_full_text_v1_ko.csv"
)
TEXT_REPORT = (
    PROJECT / "workspace" / "analysis" / "full_translation_current_v44_20260829_a.json"
)
GRAPHICS_REPORT = (
    PROJECT / "workspace" / "analysis" / "full_translation_graphics_v44_20260829_a.json"
)
XDELTA_SOURCE = (
    PROJECT.parents[1] / "Tools" / "xdelta3-3.2.0-windows-x86_64" / "xdelta3.exe"
)
XDELTA_README = XDELTA_SOURCE.with_name("README.md")
DIST_ROOT = PROJECT / "dist"
RELEASE_DIR = DIST_ROOT / RELEASE_NAME
ARCHIVE = DIST_ROOT / f"{RELEASE_NAME}.zip"
ARCHIVE_HASH = DIST_ROOT / f"{RELEASE_NAME}.zip.sha256"
PATCH_NAME = f"AnotherCode_KR_{RELEASE_LABEL}_{RELEASE_DATE}.xdelta"

SOURCE_SHA256 = "6C8CB19DE7B86DC5F1147E4927D6916571968A06B5C2E9B88F62C22C271DD0A1"
TARGET_SHA256 = "28B914E99A54B74576F0684A49898C89989EE92D2566D6683E6C1246B9711E9D"
CSV_SHA256 = "0DAD428F502F6231AF5383C02E26454BF47D5E81ACC7951DF342E69C6008A861"
ROM_SIZE = 33_554_432


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def verify_input(path: Path, expected_hash: str, expected_size: int | None = None) -> None:
    if not path.is_file():
        raise RuntimeError(f"missing required input: {path}")
    if expected_size is not None and path.stat().st_size != expected_size:
        raise RuntimeError(f"unexpected size for {path}: {path.stat().st_size}")
    actual = sha256(path)
    if actual != expected_hash:
        raise RuntimeError(f"unexpected SHA-256 for {path}: {actual}")


def apply_cmd() -> str:
    return """@echo off
setlocal
chcp 65001 >nul
if "%~1"=="" (
  echo Drag the original Another Code Japanese .nds file onto apply.cmd.
  echo Or run: apply.cmd "Another Code 2tsu no Kioku.nds"
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0apply.ps1" "%~f1"
set "RESULT=%ERRORLEVEL%"
if /I not "%AC_PATCH_NO_PAUSE%"=="1" pause
exit /b %RESULT%
"""


def apply_ps1() -> str:
    return f"""param([Parameter(Mandatory = $true)][string]$InputRom)

$ErrorActionPreference = 'Stop'
try {{ [Console]::OutputEncoding = [Text.Encoding]::UTF8 }} catch {{ }}

$source = (Resolve-Path -LiteralPath $InputRom).Path
$base = [IO.Path]::GetFileNameWithoutExtension($source)
$dir = Split-Path -Parent $source
$output = Join-Path $dir ($base + '_KR.nds')
$temporary = Join-Path $dir ($base + '_KR.tmp.nds')
$patch = Join-Path $PSScriptRoot '{PATCH_NAME}'
$xdelta = Join-Path $PSScriptRoot 'tools/xdelta3.exe'

try {{
    $sourceItem = Get-Item -LiteralPath $source
    $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash
    if ($sourceItem.Length -ne {ROM_SIZE} -or $sourceHash -ne '{SOURCE_SHA256}') {{
        Write-Host ''
        Write-Host '[실패] 지원하지 않는 원본 ROM입니다.' -ForegroundColor Red
        Write-Host "  파일: $source"
        Write-Host "  크기: $($sourceItem.Length) 바이트"
        Write-Host "  SHA-256: $sourceHash"
        Write-Host '  필요한 판본: Another Code: 2tsu no Kioku (Japan), 33,554,432바이트'
        Write-Host '  ROM 타이틀/게임 코드: ANOTHERCODE / ANOJ'
        Write-Host '  필요한 SHA-256: {SOURCE_SHA256}'
        exit 1
    }}

    if (Test-Path -LiteralPath $temporary) {{ Remove-Item -LiteralPath $temporary -Force }}
    & $xdelta -d -f -s $source $patch $temporary
    if ($LASTEXITCODE -ne 0) {{ throw "xdelta 오류 (종료 코드 $LASTEXITCODE)" }}

    $outputHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $temporary).Hash
    if ($outputHash -ne '{TARGET_SHA256}') {{
        throw "패치 결과 SHA-256 불일치: $outputHash"
    }}

    Move-Item -LiteralPath $temporary -Destination $output -Force
    Write-Host ''
    Write-Host '[완료] 한글 패치를 적용했습니다.' -ForegroundColor Green
    Write-Host "  결과: $output"
    Write-Host '  SHA-256: {TARGET_SHA256}'
    exit 0
}} catch {{
    if (Test-Path -LiteralPath $temporary) {{ Remove-Item -LiteralPath $temporary -Force }}
    Write-Host ''
    Write-Host "[실패] $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}}
"""


def readme() -> str:
    return f"""========================================================================
 Another Code: 2tsu no Kioku 한국어 패치 {RELEASE_LABEL} (2026-08-29)
 대상: 일본판 Another Code: 2tsu no Kioku / Nintendo DS
========================================================================

[구성]

  {PATCH_NAME}    ROM 패치
  apply.cmd                               패치 적용 창구
  apply.ps1                               판본 확인 및 패치 적용 스크립트
  tools\\xdelta3.exe                      xdelta 3.2.0
  SHA256.txt                              배포 파일 무결성 목록
  release_report.json                     빌드 및 검증 기록

원본 ROM과 패치 완료 ROM, 세이브 및 세이브스테이트는 배포물에 포함되어 있지 않습니다.

[적용 방법]

1. 일본판 원본 .nds 파일을 준비합니다.
2. 원본 .nds 파일을 apply.cmd 위로 끌어다 놓습니다.
3. 원본 파일과 같은 폴더에 <원본 파일명>_KR.nds가 생성됩니다.

명령 프롬프트에서는 다음처럼 실행할 수도 있습니다.

  apply.cmd "Another Code 2tsu no Kioku.nds"

지원 원본:
  파일 크기: 33,554,432바이트
  ROM 타이틀: ANOTHERCODE
  게임 코드: ANOJ
  SHA-256: {SOURCE_SHA256}

패치 결과:
  파일 크기: 33,554,432바이트
  SHA-256: {TARGET_SHA256}

다른 지역판이나 다른 덤프에는 적용되지 않습니다. 적용 스크립트가 원본의 SHA-256을
먼저 확인하므로 잘못된 판본에는 결과 ROM을 만들지 않습니다.

[번역 및 변경 범위]

  - 본문·선택지·시스템 문구·크레딧을 포함한 텍스트 2,748개
  - 본문용 한글 825자 글꼴과 포인터/문자열 재배치
  - 타이틀, 시스템 버튼, PDA, 조사 화면 등 그래픽 캔버스 65개
  - 플레이 중 확인된 일본어 UI 잔재와 일부 크레딧 라벨 보정

[검증 범위]

현재 최종 ROM은 깨끗한 일본판 원본에서 두 번 독립 재생성되어 바이트 단위로
일치했으며 다음 기계 검증을 통과했습니다.

  - 번역 CSV 2,789행 구조와 보호 필드, 제어 지시문 검사
  - 36개 텍스트 재배치 클러스터와 포인터·글꼴·등록 쓰기 범위 검사
  - 65/65 그래픽 캔버스의 압축 왕복, 팔레트, 리소스 읽기 검증
  - 자동 테스트 72개
  - xdelta 역적용 결과가 최종 ROM과 바이트 단위로 동일함을 확인
  - 공백과 한글이 포함된 경로에서 실제 apply.cmd 적용 확인

다만 이 최종 번역 수정본으로 처음부터 엔딩까지 전 구간 실플레이 검수는 완료되지
않았습니다. 플레이 중 줄바꿈, 장면 전환, 특정 선택지나 그래픽 표시 문제가 보이면
장면과 스크린샷을 함께 기록해 주세요.

[세이브 데이터]

에뮬레이터에 따라 세이브 파일명이 ROM 파일명에 연결됩니다. 기존 .sav를 사용하려면
원본을 별도 보관한 뒤 새 ROM의 파일명과 맞춰 복사하세요. 이전 ROM의 세이브스테이트
(.ml1 등)는 메모리 상태까지 포함하므로 최종 ROM 검증에는 사용하지 않는 것을 권장합니다.

[주의 및 저작권]

패치 결과 ROM을 배포하지 마십시오. xdelta 패치 파일만 공유할 수 있습니다.
이 패치는 비공식 팬 번역이며 게임 및 관련 상표의 권리는 Nintendo에 있습니다.

xdelta3 3.2.0은 Apache License 2.0 계열입니다(tools\\README_xdelta3.md).
Galmuri 글꼴은 Lee Minseo가 제작했으며 SIL Open Font License 1.1입니다.
글꼴 파일 자체는 포함하지 않고 변환된 비트맵 데이터만 패치에 들어갑니다.
"""


def write_utf8_bom(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8-sig", newline="\r\n")


def count_changed_bytes(left: Path, right: Path) -> int:
    changed = 0
    with left.open("rb") as a, right.open("rb") as b:
        while True:
            left_chunk = a.read(1024 * 1024)
            right_chunk = b.read(1024 * 1024)
            if not left_chunk and not right_chunk:
                break
            if len(left_chunk) != len(right_chunk):
                raise RuntimeError("source and target sizes differ")
            changed += sum(x != y for x, y in zip(left_chunk, right_chunk))
    return changed


def create_archive(release_dir: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(release_dir.rglob("*"), key=lambda p: p.as_posix().lower()):
            if not path.is_file():
                continue
            relative = Path(RELEASE_NAME) / path.relative_to(release_dir)
            info = zipfile.ZipInfo(relative.as_posix(), (2026, 8, 29, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            zf.writestr(info, path.read_bytes(), compresslevel=9)


def build_release() -> dict:
    verify_input(SOURCE_ROM, SOURCE_SHA256, ROM_SIZE)
    verify_input(TARGET_ROM, TARGET_SHA256, ROM_SIZE)
    verify_input(TRANSLATION_CSV, CSV_SHA256)
    for required in (TEXT_REPORT, GRAPHICS_REPORT, XDELTA_SOURCE, XDELTA_README):
        if not required.is_file():
            raise RuntimeError(f"missing required input: {required}")

    text_report = json.loads(TEXT_REPORT.read_text(encoding="utf-8"))
    graphics_report = json.loads(GRAPHICS_REPORT.read_text(encoding="utf-8"))
    if text_report["output"]["sha256"] != graphics_report["base_translation"]["sha256"]:
        raise RuntimeError("text and graphics reports do not form one build chain")
    if graphics_report["output"]["sha256"] != TARGET_SHA256:
        raise RuntimeError("graphics report does not describe the release target")
    if RELEASE_DIR.exists() or ARCHIVE.exists() or ARCHIVE_HASH.exists():
        raise RuntimeError(f"release output already exists: {RELEASE_NAME}")

    with TRANSLATION_CSV.open("r", encoding="utf-8-sig", newline="") as stream:
        csv_rows = sum(1 for _ in csv.DictReader(stream))

    tools_dir = RELEASE_DIR / "tools"
    tools_dir.mkdir(parents=True)
    patch_path = RELEASE_DIR / PATCH_NAME
    subprocess.run(
        [str(XDELTA_SOURCE), "-e", "-f", "-s", str(SOURCE_ROM), str(TARGET_ROM), str(patch_path)],
        check=True,
    )
    shutil.copy2(XDELTA_SOURCE, tools_dir / "xdelta3.exe")
    shutil.copy2(XDELTA_README, tools_dir / "README_xdelta3.md")
    (RELEASE_DIR / "apply.cmd").write_text(apply_cmd(), encoding="ascii", newline="\r\n")
    write_utf8_bom(RELEASE_DIR / "apply.ps1", apply_ps1())
    write_utf8_bom(RELEASE_DIR / "README_적용방법.txt", readme())

    with tempfile.TemporaryDirectory(prefix="another_code_xdelta_verify_") as raw_tmp:
        decoded = Path(raw_tmp) / "decoded.nds"
        subprocess.run(
            [str(XDELTA_SOURCE), "-d", "-f", "-s", str(SOURCE_ROM), str(patch_path), str(decoded)],
            check=True,
        )
        verify_input(decoded, TARGET_SHA256, ROM_SIZE)

    work_root = PROJECT / "work"
    work_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="배포 적용 테스트 ", dir=work_root) as raw_tmp:
        test_dir = Path(raw_tmp) / "원본 ROM"
        test_dir.mkdir()
        test_source = test_dir / "Another Code 일본판 원본.nds"
        shutil.copy2(SOURCE_ROM, test_source)
        environment = os.environ.copy()
        environment["AC_PATCH_NO_PAUSE"] = "1"
        cmd_result = subprocess.run(
            ["cmd.exe", "/d", "/c", "call", str(RELEASE_DIR / "apply.cmd"), str(test_source)],
            cwd=RELEASE_DIR,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if cmd_result.returncode != 0:
            raise RuntimeError(
                "actual apply.cmd test failed: " + (cmd_result.stdout + cmd_result.stderr).strip()
            )
        verify_input(test_dir / "Another Code 일본판 원본_KR.nds", TARGET_SHA256, ROM_SIZE)

    report = {
        "schema_version": 1,
        "release_stage": "distribution",
        "release_name": RELEASE_NAME,
        "release_date": RELEASE_DATE,
        "status": "PASS: deterministic static build and patch verification; full-game manual runtime QA pending",
        "contains_original_rom": False,
        "contains_patched_rom": False,
        "contains_save_or_savestate": False,
        "source": {
            "name": SOURCE_ROM.name,
            "size": ROM_SIZE,
            "header_title": "ANOTHERCODE",
            "game_code": "ANOJ",
            "sha256": SOURCE_SHA256,
        },
        "target": {"size": ROM_SIZE, "sha256": TARGET_SHA256},
        "translation_csv": {
            "name": TRANSLATION_CSV.name,
            "sha256": CSV_SHA256,
            "rows": csv_rows,
        },
        "build_reports": {
            "text": {"name": TEXT_REPORT.name, "sha256": sha256(TEXT_REPORT)},
            "graphics": {"name": GRAPHICS_REPORT.name, "sha256": sha256(GRAPHICS_REPORT)},
        },
        "patch": {
            "name": PATCH_NAME,
            "size": patch_path.stat().st_size,
            "sha256": sha256(patch_path),
            "reverse_verification": "PASS",
        },
        "rom_changes": {
            "changed_byte_positions": count_changed_bytes(SOURCE_ROM, TARGET_ROM),
            "text_records": text_report["scope"]["record_count"],
            "hangul_characters": text_report["mapping"]["character_count"],
            "graphics_canvases": graphics_report["graphics"]["target_count"],
        },
        "verification": {
            "deterministic_build_a_b": "PASS",
            "static_build": "PASS",
            "unit_tests_72": "PASS",
            "xdelta_reverse_apply": "PASS",
            "actual_cmd_apply_from_space_and_korean_path": "PASS",
            "full_game_manual_runtime": "PENDING",
        },
    }
    write_utf8_bom(
        RELEASE_DIR / "release_report.json",
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )

    manifest_files = sorted(
        (p for p in RELEASE_DIR.rglob("*") if p.is_file()),
        key=lambda p: p.relative_to(RELEASE_DIR).as_posix().lower(),
    )
    manifest = "".join(
        f"{sha256(path)}  {path.relative_to(RELEASE_DIR).as_posix()}\n"
        for path in manifest_files
    )
    (RELEASE_DIR / "SHA256.txt").write_text(
        manifest, encoding="utf-8-sig", newline="\r\n"
    )
    create_archive(RELEASE_DIR, ARCHIVE)
    ARCHIVE_HASH.write_text(
        f"{sha256(ARCHIVE)}  {ARCHIVE.name}\r\n", encoding="ascii"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    report = build_release()
    print(json.dumps({
        "status": "pass",
        "release_dir": str(RELEASE_DIR),
        "archive": str(ARCHIVE),
        "archive_sha256": sha256(ARCHIVE),
        "patch_sha256": report["patch"]["sha256"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
