# Another Code 전체 텍스트 CSV 사용법

## 기준 파일

- 추출 기준선: `another_code_full_text_v1.csv`
- 사용자 최종 번역 SSOT: `another_code_full_text_v1_ko.csv`
- 인코딩: UTF-8 BOM, RFC 4180 호환 인용
- 원본 ROM SHA-256: `6C8CB19DE7B86DC5F1147E4927D6916571968A06B5C2E9B88F62C22C271DD0A1`
- 생성 보고서: `../../analysis/translation_csv_export_v1.json`

## 번역자가 편집하는 열

- `translation_text`: 한국어 번역. `source_text`의 `%...` 제어코드와 순서·인수를 그대로 보존합니다.
- `translation_status`: `not_started`, `draft`, `reviewed`, `approved` 등 작업 상태입니다.
- `speaker_names`: 화자 코드의 실제 이름이 확인되면 입력합니다.
- `translator_notes`: 문맥, 말투, 조판 메모를 입력합니다.

그 외 열은 원문 식별·재삽입·검증용 보호 열입니다. 특히 `text_id`, `source_text`,
`source_raw_hex`, 길이, 포인터, 제어코드 열을 수정하지 않습니다.
내보내기 도구는 기본적으로 기존 CSV 덮어쓰기를 거부합니다. 재추출 감사본은 `work/`에
새 파일명으로 만들고, 번역이 시작된 이 SSOT를 재생성 파일로 덮어쓰지 않습니다.

`another_code_full_text_v1_ko.csv`는 사용자가 직접 편집한 최종 문안입니다. 빌드 도구는
이를 읽기만 하며 문장, 상태 또는 보호 열을 자동 수정하지 않습니다. 현재 기준선 대비
읽기 전용 감사 결과는 `../../analysis/translation_csv_ko_audit_v1.json`입니다.

현재 감사 결과 strong 2,059행과 auxiliary 689행은 모두 번역이 채워졌습니다. 공란
36행은 전부 재삽입 금지 `isolated_candidate`입니다. `%...` 제어 토큰 불일치는 0건이며,
고유 완성형 한글 수요는 824자입니다. `·` 6건은 CSV를 고치지 않고 build-layer 인코딩
alias 후보로 관리합니다.

## 모집단

- `strong_script` 2,059행: terminal `%e` 스크립트. 고정 길이 재삽입의 핵심 대상입니다.
- `auxiliary` 689행: UI·명칭으로 보이는 인접 문자열 표. 소비 코드와 화면 확인이 더 필요합니다.
- `isolated_candidate` 41행: 오탐 가능성이 있는 검토 후보입니다. `not_build_eligible`이며 번역하지 않습니다.

## scene과 화자 정보

- `scene_id`: `overlay_000` 같은 구조적 그룹입니다. 실제 게임 재생 순서를 뜻하지 않습니다.
- `scene_order`: overlay 번호이며 장면 정렬 보조값입니다.
- `asset_scene_ids`: `%i(Pxxxx)`/`%n(Pxxxx)`에서 얻은 `Pxx` 자산 그룹입니다.
- `event_ids`: `%v(...)` 이벤트 번호입니다.
- `speaker_codes`: 레코드에서 관측된 `%C(...)` 코드의 중복 제거 목록입니다.
- `speaker_sequence`: 한 레코드 안에서 화자 코드가 바뀌는 순서입니다.
- `speaker_status`: 단일/복수/미검출 상태입니다.

`%C`의 정확한 인물 이름 의미는 아직 런타임으로 입증되지 않았으므로 `speaker_names`는
비워 두었습니다. 한 레코드에 여러 화자가 포함될 수 있어 한 사람으로 자동 단정하지 않습니다.

## 번역 시 주의

1. `source_preview`는 읽기 편하게 제어코드를 숨긴 참고 열입니다. 빌드 기준은 `source_text`입니다.
2. `%r`, `%h`, `%e`, `%C(...)` 등 모든 `%...` 토큰을 삭제·이동·수정하지 않습니다.
3. 현재는 고정 길이 PoC만 검증됐으므로 번역 길이 제한과 재배치는 후속 빌드 단계에서 처리합니다.
4. `source_roundtrip_equal=no`인 행은 CP932 중복 매핑 후보이며 재삽입 대상이 아닙니다.
5. NitroFS의 불투명 pack 리소스와 그래픽에 구워진 글자는 아직 이 CSV 범위 밖입니다.
