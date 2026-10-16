# Another Code 한글 대사 편집기

프로젝트 루트의 `start_dialogue_editor.cmd`를 실행하면 사용자 번역 SSOT인
`workspace/text/translation/another_code_full_text_v1_ko.csv`를 엽니다.

## 주요 기능

- 한 창에서 `원문·번역 목록 | 번역 편집 | NDS 미리보기/검사`를 동시에 표시합니다.
- 왼쪽 목록은 제어코드를 숨긴 원문과 번역을 나란히 보여주며 가로 스크롤을 지원합니다.
- 원문과 보호 메타데이터를 그대로 두고 `translation_text`, `translation_status`,
  `speaker_names`, `translator_notes`만 편집합니다.
- `%C(0_xxx)`는 주인공 계열로 분류해 아래 대사창에, 다른 `%C(...)`는 위 대사창에 표시합니다.
- `%h`는 다음 대사창, `%r`은 같은 창의 줄바꿈으로 미리봅니다.
- 실제 ARM9 창 코드에 따라 2줄 대화창, 1줄 메시지창, 항목/정보창,
  창 없는 자막, 전체 화면 문서를 자동 구분합니다.
- 미리보기는 게임과 같은 12px 글리프 전진폭, 16px 줄 간격 및 30타일 창 폭을 사용합니다.
- 소비 화면이 미확정인 항목은 글자 수와 줄 수를 바꾸고 `현재 항목 규칙 저장`으로
  `another_code_layout_profiles.json`의 `text_id`별 override에 보존할 수 있습니다.
- 저장 전 제어코드 순서가 원문과 같은지 검사하며, 기존 CSV는
  `backups/dialogue_editor`에 시각이 붙은 이름으로 백업합니다.

## 실행

```powershell
start_dialogue_editor.cmd
```

다른 CSV를 열려면:

```powershell
python -X utf8 tools\dialogue_editor\another_code_dialogue_editor.py "경로\파일.csv"
```

GUI를 열지 않고 정적 검사만 하려면:

```powershell
python -X utf8 tools\dialogue_editor\another_code_dialogue_editor.py --check
python -X utf8 -m unittest discover -s tools\dialogue_editor -p "test_*.py"
```

## 자동 판정 상한

| 소비 유형 | 자동 식별 | 한글 상한 | 근거 |
|---|---|---:|---|
| 2줄 대화창 | `%w(1xx)`, `%w(3xx)`, `%w(5xx)` | 17자 × 2줄 | 30×6타일 창, 안쪽 208×32px |
| 1줄 메시지창 | `%w(2xx)`, `%w(4xx)` | 17자 × 1줄 | 30×3타일 창, 안쪽 208×16px |
| 항목/정보창 | 명시적 `%w`가 없는 strong script | 17자 × 2줄 | ARM9 항목 패널 원문 경계와 공통 렌더러 |
| 창 없는 자막 | `%w(00n)` | `19-n`자 × 2줄 | 시작 X=`24+12n`, 화면 우측 경계 |
| 전체 화면 문서/인트로 | overlay 000 자유 배치 | 19자 × 최대 8줄 | 화면 물리 폭과 원문 템플릿 최대 줄 수 |
| auxiliary/isolated | 후보 테이블 | 검사 보류 | 소비 함수와 화면 폭이 미추적 |

17자 계산은 `(안쪽 폭 208px - 글리프 폭 16px) / 전진폭 12px + 1`입니다.
`auxiliary` 689개와 `isolated_candidate` 41개는 하나의 공통 UI가 아니라 휴리스틱으로
모은 후보이므로, 기존의 임의 16자 제한을 제거했습니다. 해당 행은 화면 소비자를 추적해
항목별 override를 만들기 전까지 노란색 `검사 보류`로 표시됩니다.

측정 보고서는 `workspace/analysis/dialogue_layout_profiles_v1.json`이며, 재생성 명령은 다음과 같습니다.

```powershell
python -X utf8 workspace\scripts\analyze_dialogue_layout_profiles.py
```

정적 미리보기 통과는 melonDS 런타임 QA를 대신하지 않습니다.
