# 그래픽 추출 기준

현재 기준 산출물은 `pack_v2`입니다. 원본 pack의 47개 archive, 1,319개 resource를
전부 해제했으며 다음 두 계열을 PNG로 변환했습니다.

- 직접 인덱스 이미지: 1,029개 (`png/`)
- `HEAD` 모델과 대응 `*_tex` 115쌍에서 분리한 3D 텍스처: 384개 (`model_textures/`)
- 검토 가능한 PNG 합계: 1,413개
- 아직 별도 형식인 resource: 60개

`decoded/`의 1,319개 파일은 압축 해제 직후의 원본 바이트입니다. `png/`는 NDS 2D
8×8 타일 배열, `model_textures/`는 `TEX` 메타데이터가 지정한 선형 3D 텍스처 배열을
사용합니다. 대표 배경·캐릭터·아이템·PDA·맵·미스터리 자원으로 두 배열을 시각
확인했습니다.

## 문자 검수

기존 `pack_v2/contact_sheets`는 형식과 모집단 확인용 축소 연락판이라 작은 일본어를
판독하는 용도로 쓰지 않습니다. `review_v2/index.html`은 1,413개 이미지를 원본 픽셀의
1~8배 정수 배율로 표시하고 자원명·archive·2D/3D·우선순위 필터를 제공합니다.
`open_review.cmd`를 실행하면 기본 브라우저로 엽니다.

자동 점수는 일본어 존재 판정이 아니라 검토 순서입니다. 현재 high 48개, medium 127개,
low 1,238개이며 단색·거의 빈 버퍼는 강등했습니다. 정적 후보 연락판은
`review_v2/candidate_sheets`에 있습니다.

확대 시각 검수에서 `direct:pda/mark`의 `(44,44,20,7)` 영역에 빨간색 `もどる`를
확인했습니다. 안정 ID와 픽셀 경계는 `baked_text_blocks_v1.csv`에 기록했습니다.
`mystery/R5202`는 영문·숫자, 책 퍼즐은 기호 문자이며 현재 일본어 대상으로 승인하지
않았습니다.

1. 갤러리에서 high, medium을 먼저 검토하고 발견한 블록을
   `baked_text_blocks_v1.csv`에 픽셀 경계별로 추가합니다.
2. 실제 편집본은 추출 PNG를 덮어쓰지 않고 향후 `translation/` 아래에 같은 자원 ID로
   저장합니다.
3. 현재 승인된 13개는 PNG→인덱스/팔레트 역변환과 pack 재삽입 정적 검증을
   통과했습니다. 다음은 고유 QA ROM을 melonDS에서 clean boot해 각 장면을 확인합니다.

`catalog.csv`는 1,319개 resource의 wrapper·해제 해시·형식·PNG 경로를,
`model_texture_catalog.csv`는 384개 텍스처의 모델/원시 payload ID·크기·bpp·offset을
담습니다. `report.json`은 전수 개수와 아직 증명하지 않은 범위를 기록합니다.

## 재현

```powershell
python -X utf8 .\workspace\scripts\extract_pack_graphics.py `
  --pack-dir .\extracted\nitrofs\data\pack `
  --output-root .\workspace\graphics\pack_v2

python -X utf8 .\workspace\scripts\build_graphics_review.py `
  --pack-root .\workspace\graphics\pack_v2 `
  --output-root .\workspace\graphics\review_v2

python -X utf8 .\workspace\scripts\build_graphics_pack_qa.py `
  --source-rom ".\original\Another Code 2tsu no Kioku.nds" `
  --source-config .\workspace\config\source_rom.json `
  --base-rom .\build\another_code_ko_full_translation_dual_font_qa_v1.nds `
  --base-report .\workspace\analysis\full_translation_dual_font_qa_v1.json `
  --pack-root .\workspace\graphics\pack_v2 `
  --canvas-config .\workspace\config\graphics_edit_transforms_v6.csv `
  --translated-dir .\workspace\graphics\translate_refined_v10\indexed `
  --output-rom .\build\another_code_ko_full_translation_graphics_v10_qa.nds `
  --report .\workspace\analysis\full_translation_graphics_v10_qa.json
```

추출기는 비어 있지 않은 출력 폴더를 덮어쓰지 않습니다. 재검증할 때는 `pack_v3`처럼
새 출력 폴더를 사용합니다.

## 기준 해시

- `pack_v2/catalog.csv`: `00AD733597FCE0205F304B80E058BF214F1D9BBC5AE20B7F1144916F1252CD1D`
- `pack_v2/model_texture_catalog.csv`: `F2CA87ABD19F5C5F1AC511BCE5972121F0E2E34F01CF33053AC751E6A91CEA57`
- `pack_v2/report.json`: `D2B67DC88E4A6C8D1B586C78F73F41A5A47B070CC7677440B755F9F95F608DF3`
- `review_v2/graphics_text_review.csv`: `BA16416B4896FCE3E0192EB0F257F8D914828F9DA5EA53114F011022E90C3C32`
- `review_v2/report.json`: `8AFEE575BBFE77F892B116AD2F7D77E26285F80974090B7283F1A9A1956874A4`

13개 승인 PNG의 재인코딩, pack 재압축·재삽입과 ROM 정적 readback은 완료했습니다.
수정 이미지의 에뮬레이터 런타임 소비와 나머지 그래픽 텍스트 모집단은 아직 미검증입니다.
