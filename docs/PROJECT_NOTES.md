# Another Code: 2tsu no Kioku 한국어화 프로젝트

대상은 일본판 Nintendo DS ROM `ANOJ`입니다. 원본 ROM은
`original/Another Code 2tsu no Kioku.nds`에 보존하며 직접 수정하지 않습니다.

## 구조

- `original/`: 수정하지 않는 원본 ROM
- `extracted/`: 원본에서 다시 만들 수 있는 ARM9/ARM7, overlay, NitroFS, banner 추출물
- `workspace/analysis/`: 조사 결과, 자산 맵, 가설과 판정 기록
- `workspace/config/`: 원본 식별 정보와 향후 빌드 설정
- `workspace/font/`: 한글 폰트 소스·매핑·생성 규칙
- `workspace/graphics/`: 이미지에 포함된 텍스트 작업물
- `workspace/text/source/`: 재추출 가능한 원문 데이터
- `workspace/text/translation/`: 번역 SSOT
- `workspace/asm/`: ARM9/ARM7/overlay 코드 패치 소스
- `workspace/scripts/`: 분석·추출·빌드용 정식 스크립트
- `workspace/tests/`: 라운드트립·회귀 테스트
- `build/`: 로컬 테스트 ROM과 빌드 보고서
- `dist/`: 배포 패치와 README, 해시, 압축 파일
- `qa/`: 에뮬레이터 로그·세이브 상태·스크린샷
- `tools/`: 프로젝트에 고정한 외부 도구 또는 도구 설명
- `work/`: 폐기 가능한 임시 작업물
- `backups/`: 작업 중 수동 백업
- `docs/`: 현재 상태와 인수인계 문서

## 런타임 QA 환경

사용자 지정 NDS 에뮬레이터 루트는 `H:\NDS`입니다. 확인된 실행 파일은
`melonDS.exe`와 `DeSmuME-VS2022-x64-Release.exe`이며, 정확한 식별 정보는
`workspace/config/qa_environment.json`에서 관리합니다. 빌드 ROM은 고유한 파일명과
격리된 새 세이브로 실행하고, 정적 검사와 실제 런타임 결과를 구분해 `qa/`에 기록합니다.

## 현재 상태

2026-08-29 최종 배포 후보를 생성했습니다. 최종 ROM은
`build/another_code_ko_final_20260829.nds`(SHA-256
`28B914E99A54B74576F0684A49898C89989EE92D2566D6683E6C1246B9711E9D`)이며,
ROM을 포함하지 않은 배포 ZIP은
`dist/AnotherCode_KR_Patch_Final_20260829.zip`입니다. 동일 입력 A/B 빌드,
65/65 그래픽 정적 검사, 자동 테스트 72개, xdelta 역적용, 한글·공백 경로에서
ZIP 내부 `apply.cmd` 실제 적용이 통과했습니다. 최신 수정본 전체의 처음부터 엔딩까지
실플레이 검수는 아직 완료되지 않았습니다. 상세 상태는
`docs/HANDOFF_2026-08-29.md`와 배포물의 `release_report.json`을 확인합니다.

### 이전 조사 기록

원본 식별, NDS 구조 인벤토리, 강한 스크립트 2,059개와 보조 문자열 689개의
직접 포인터 카탈로그, `font_lc.bin` 형식 분석을 완료했습니다. melonDS 1.1에서
Galmuri `한`의 실제 주 대화 렌더링이 통과했습니다. 처음에는 `%v(006)` 레코드를
최초 대화로 오인했지만 사용자 런타임으로 폐기했습니다. 실제 최초 자동 화면은
overlay 0의 `１３才最後の朝 / 気分は最悪だった`로 확인됐습니다. 이를
`열셋마지막아침 / 기분은최악이었다`로 바꾼 인트로 PoC가 정적 검증을 통과했고,
사용자 런타임 확인으로 기존 두 번째 번역이 실제 3번째 화면임을 판명했고, 누락된
2번째 화면을 포인터 표적으로 복구했습니다. 첫 3화면을 포함한 PoC는 melonDS에서
세 화면 모두 정상 출력되어 런타임 게이트를 통과했습니다. 사용자가 완성한 `_ko.csv`는 보호 열과
제어코드 감사에 통과했으며, accepted 2,748행은 모두 번역됐습니다. 36개 component의
기존 문자열 영역과 0~3바이트 zero alignment를 이용하면 파일 성장 없이 전량 배치
가능하다는 정적 용량 분석도 통과했습니다. 이 모델을 writer PoC로 구현했습니다.
재배치 writer를 구현해 최종 CSV의 첫 3화면을 적용한
`another_code_ko_intro_csv_relocation_poc_v1.nds`를 만들었고, 사용자가 세 화면 모두
정상 출력됨을 확인했습니다. 4,375개 글리프 코드를 전수 감사해 824자 정적 후보를
선정할 용량도 확인했습니다. 새로 쓰는 lead
81~88·96과 기존 97~98 대표 글리프는 사용자 런타임 스크린샷에서 모두 정상
표시됐습니다. 기존 인트로 45자 코드를 보존한 824자 결정적 후보 매핑도 생성했습니다.
pack의 47 archive/1,319 resource 디렉터리를 복구하고 ARM9 `0x02026B9C` 압축기를
재구현해 압축 resource 1,147개를 전부 해제했습니다. 강한 terminal 스크립트 충돌은
0건입니다. `%u`가 `font.bin`/`font_lc.bin`을 전환하므로 두 폰트를 함께 패치하는
전체 QA 후보도 만들었습니다. 이 후보는 2,748행/36 component, 직접 포인터 2,982개,
824글리프×2 폰트의 정적 readback을 통과했고, 사용자가 clean boot와 요청한 인트로
출력을 정상으로 확인했습니다. 전체 2,748행·layout·전체 게임은 아직 검증하지 않았으므로
release가 아닙니다. pack의 직접 인덱스 이미지 1,029개와 `HEAD` 모델의 3D 텍스처
384개를 `workspace/graphics/pack_v2`에 PNG로 추출했습니다. 승인된 그래픽 13개는
역변환·type-1 재압축·archive 재배치와 ROM 재삽입까지 정적 검증했으며,
그래픽 수정본의 런타임 검증과 배포 패치는 아직입니다. 축소 연락판 대신 1~8배 확대
검색이 가능한 `workspace/graphics/review_v2/index.html`을 만들었고,
`direct:pda/mark`에서 일본어 `もどる`를 첫 확정 그래픽 텍스트로 확인했습니다.
현재 그래픽 텍스트 목록 v8은 이 항목을 `뒤로` 번역 후보로 포함한 13행이며,
`translate_refined_v10`은 `pda_mark`를 8×8 타일 스트림 순서로 결합해 버튼 외곽을
한 줄로 복원하고 9px 글자를 버튼 중심에 배치한 13개 편집 결과를 제공합니다.
전체 번역과 그래픽을 합친 QA ROM은
`build/another_code_ko_full_translation_graphics_v10_qa.nds`, SHA-256
`1AA4EFC08FA95050A4F833201FFA3AD128D264DEACB76BF97654D0A83F948AD3`입니다.
현재 54개 테스트가 통과합니다.

번역 작업용 전체 카탈로그의 추출 기준선은
`workspace/text/translation/another_code_full_text_v1.csv`, 사용자 최종
번역 SSOT는 `workspace/text/translation/another_code_full_text_v1_ko.csv`입니다. 강한 스크립트 2,059행,
보조 UI·명칭 후보 689행, 격리 검토 후보 41행을 포함한 2,789행이며, scene·이벤트·화자 코드와
원문 바이트·포인터·제어코드를 함께 보존합니다. 편집 규칙은 같은 폴더의
`README_translation_csv.md`를 따릅니다.

확정 한글 폰트는
`G:\Downloads\Korean Traslate Project\Font\Galmuri11-Condensed_16x16(12px).png`이며,
SHA-256은 `3EE2A45C20120C42705AE3EE9540DB191A1443E110ECA6D0C4A1760CBE0B91E5`입니다.
16×16 셀을 직접 가져오며 별도 2배 확대를 하지 않습니다.
현재 사실과 다음 작업은 `workspace/analysis/SURVEY.md`와
`docs/HANDOFF_2026-08-23.md`를 기준으로 확인합니다.

작업을 끝낼 때마다 최신 `docs/HANDOFF_YYYY-MM-DD.md`를 갱신하거나 새로 작성해
변경 파일, 해시, 검증/미검증 경계, 재현 명령과 다음 작업을 남깁니다.

## 원칙

1. `original/`의 ROM을 입력으로만 사용하고 덮어쓰지 않습니다.
2. 추출·빌드 결과는 `extracted/`, `work/`, `build/`에 새로 생성합니다.
3. 확정한 오프셋이나 명령 패치에는 원본 해시와 expected bytes 검증을 붙입니다.
4. 정적 검사와 빌드 성공을 에뮬레이터 런타임 검증으로 기록하지 않습니다.

## 재현 명령

```powershell
python -X utf8 -m unittest discover -s .\workspace\tests -p "test_*.py"
python -X utf8 .\workspace\scripts\nds_inventory.py inventory --rom ".\original\Another Code 2tsu no Kioku.nds" --report .\workspace\analysis\nds_inventory.json --extract-root .\extracted
python -X utf8 .\workspace\scripts\extract_script_candidates.py --extracted-root .\extracted --inventory-report .\workspace\analysis\nds_inventory.json --report .\workspace\text\source\script_candidates.json
python -X utf8 .\workspace\scripts\analyze_text_population.py --extracted-root .\extracted --script-report .\workspace\text\source\script_candidates.json --population-report .\workspace\analysis\text_population_audit.json --aux-report .\workspace\text\source\auxiliary_candidates.json --control-report .\workspace\analysis\control_tokens.json
python -X utf8 .\workspace\scripts\analyze_text_references.py --extracted-root .\extracted --inventory-report .\workspace\analysis\nds_inventory.json --script-report .\workspace\text\source\script_candidates.json --aux-report .\workspace\text\source\auxiliary_candidates.json --report .\workspace\analysis\text_references.json
python -X utf8 .\workspace\scripts\build_font_poc.py --source-rom ".\original\Another Code 2tsu no Kioku.nds" --font-png "G:\Downloads\Korean Traslate Project\Font\Galmuri11-Condensed_16x16(12px).png" --output-rom .\build\another_code_ko_font12_poc.nds --report .\workspace\analysis\font12_poc.json --char 한
python -X utf8 .\workspace\scripts\nds_inventory.py compare --base ".\original\Another Code 2tsu no Kioku.nds" --candidate .\build\another_code_ko_galmuri7_font_poc_han_v1.nds --report .\workspace\analysis\font_poc_han_v1_diff.json
python -X utf8 .\workspace\scripts\build_dialogue_e2e.py --source-rom ".\original\Another Code 2tsu no Kioku.nds" --font-png "G:\Downloads\Korean Traslate Project\Font\Galmuri11-Condensed_16x16(12px).png" --font-config .\workspace\config\font_source.json --config .\workspace\config\intro_first_screen_poc_v1.json --translation .\workspace\text\translation\intro_first_screen_poc_v1.json --script-report .\workspace\text\source\script_candidates.json --aux-report .\workspace\text\source\auxiliary_candidates.json --reference-report .\workspace\analysis\text_references.json --output-rom .\build\another_code_ko_intro_font12_condensed_ab_v1.nds --report .\workspace\analysis\intro_font12_condensed_ab_v1.json
python -X utf8 .\workspace\scripts\generate_hangul_mapping.py --batch .\workspace\text\translation\intro_prologue_poc_v1.json --pool .\workspace\config\hangul_code_pool_intro_poc_v1.json --font-config .\workspace\config\font_source.json --script-report .\workspace\text\source\script_candidates.json --aux-report .\workspace\text\source\auxiliary_candidates.json --existing-mapping .\workspace\config\hangul_mapping_intro_poc_v1.json --output .\workspace\config\hangul_mapping_intro_poc_v1.json
python -X utf8 .\workspace\scripts\build_translation_batch.py --source-rom ".\original\Another Code 2tsu no Kioku.nds" --font-png "G:\Downloads\Korean Traslate Project\Font\Galmuri11-Condensed_16x16(12px).png" --font-config .\workspace\config\font_source.json --batch .\workspace\text\translation\intro_prologue_poc_v1.json --mapping .\workspace\config\hangul_mapping_intro_poc_v1.json --script-report .\workspace\text\source\script_candidates.json --aux-report .\workspace\text\source\auxiliary_candidates.json --reference-report .\workspace\analysis\text_references.json --output-rom .\build\another_code_ko_intro_3screen_poc_v1.nds --report .\workspace\analysis\intro_3screen_poc_v1.json
python -X utf8 .\workspace\scripts\export_translation_csv.py --script-report .\workspace\text\source\script_candidates.json --aux-report .\workspace\text\source\auxiliary_candidates.json --population-report .\workspace\analysis\text_population_audit.json --reference-report .\workspace\analysis\text_references.json --seed-translation .\workspace\text\translation\intro_prologue_poc_v1.json --output .\work\another_code_full_text_regenerated.csv --report .\work\translation_csv_export_regenerated.json
python -X utf8 .\workspace\scripts\audit_translation_csv.py --baseline .\workspace\text\translation\another_code_full_text_v1.csv --translated .\workspace\text\translation\another_code_full_text_v1_ko.csv --report .\workspace\analysis\translation_csv_ko_audit_v1.json
python -X utf8 .\workspace\scripts\analyze_translation_relocation.py --translated .\workspace\text\translation\another_code_full_text_v1_ko.csv --audit-report .\workspace\analysis\translation_csv_ko_audit_v1.json --reference-report .\workspace\analysis\text_references.json --inventory .\workspace\analysis\nds_inventory.json --extracted-root .\extracted --report .\workspace\analysis\translation_relocation_capacity_v1.json
python -X utf8 .\workspace\scripts\build_translation_relocation_poc.py --source-rom ".\original\Another Code 2tsu no Kioku.nds" --translated-csv .\workspace\text\translation\another_code_full_text_v1_ko.csv --config .\workspace\config\intro_csv_relocation_poc_v1.json --pool .\workspace\config\hangul_code_pool_intro_poc_v1.json --existing-mapping .\workspace\config\hangul_mapping_intro_poc_v1.json --font-png "G:\Downloads\Korean Traslate Project\Font\Galmuri11-Condensed_16x16(12px).png" --font-config .\workspace\config\font_source.json --script-report .\workspace\text\source\script_candidates.json --aux-report .\workspace\text\source\auxiliary_candidates.json --reference-report .\workspace\analysis\text_references.json --output-rom .\build\another_code_ko_intro_csv_relocation_poc_v1.nds --output-mapping .\workspace\config\hangul_mapping_intro_csv_relocation_poc_v1.json --report .\workspace\analysis\intro_csv_relocation_poc_v1.json --force
python -X utf8 .\workspace\scripts\analyze_full_hangul_code_pool.py --rom ".\original\Another Code 2tsu no Kioku.nds" --font .\extracted\nitrofs\data\font_lc.bin --translation-csv .\workspace\text\translation\another_code_full_text_v1_ko.csv --existing-mapping .\workspace\config\hangul_mapping_intro_csv_relocation_poc_v1.json --output .\workspace\analysis\full_hangul_code_pool_v1.json
python -X utf8 .\workspace\scripts\build_translation_batch.py --source-rom ".\original\Another Code 2tsu no Kioku.nds" --font-png "G:\Downloads\Korean Traslate Project\Font\Galmuri11-Condensed_16x16(12px).png" --font-config .\workspace\config\font_source.json --batch .\workspace\text\translation\intro_code_range_poc_v1.json --mapping .\workspace\config\hangul_mapping_intro_code_range_poc_v1.json --script-report .\workspace\text\source\script_candidates.json --aux-report .\workspace\text\source\auxiliary_candidates.json --reference-report .\workspace\analysis\text_references.json --output-rom .\build\another_code_ko_intro_code_range_poc_v1.nds --report .\workspace\analysis\intro_code_range_poc_v1.json
python -X utf8 .\workspace\scripts\generate_full_hangul_mapping.py --audit .\workspace\analysis\full_hangul_code_pool_v1.json --existing-mapping .\workspace\config\hangul_mapping_intro_csv_relocation_poc_v1.json --font-config .\workspace\config\font_source.json --runtime-result .\qa\runtime\intro-code-range-001\result.json --output .\workspace\config\hangul_mapping_full_candidate_v1.json
python -X utf8 .\workspace\scripts\analyze_pack_resources.py --pack-dir .\extracted\nitrofs\data\pack --mapping .\workspace\config\hangul_mapping_full_candidate_v1.json --font .\extracted\nitrofs\data\font_lc.bin --alternate-font .\extracted\nitrofs\data\font.bin --arm9 .\extracted\arm9\arm9.bin --output .\workspace\analysis\pack_resource_nonzero_audit_v1.json
python -X utf8 .\workspace\scripts\extract_pack_graphics.py --pack-dir .\extracted\nitrofs\data\pack --output-root .\workspace\graphics\pack_v2
python -X utf8 .\workspace\scripts\build_graphics_review.py --pack-root .\workspace\graphics\pack_v2 --output-root .\workspace\graphics\review_v2
python -X utf8 .\workspace\scripts\build_full_translation_qa.py --source-rom ".\original\Another Code 2tsu no Kioku.nds" --translated-csv .\workspace\text\translation\another_code_full_text_v1_ko.csv --mapping .\workspace\config\hangul_mapping_full_candidate_v1.json --font-png "G:\Downloads\Korean Traslate Project\Font\Galmuri11-Condensed_16x16(12px).png" --font-config .\workspace\config\font_source.json --script-report .\workspace\text\source\script_candidates.json --aux-report .\workspace\text\source\auxiliary_candidates.json --reference-report .\workspace\analysis\text_references.json --translation-audit .\workspace\analysis\translation_csv_ko_audit_v1.json --capacity-audit .\workspace\analysis\translation_relocation_capacity_v1.json --pack-audit .\workspace\analysis\pack_resource_nonzero_audit_v1.json --output-rom .\build\another_code_ko_full_translation_dual_font_qa_v1.nds --report .\workspace\analysis\full_translation_dual_font_qa_v1.json
```
