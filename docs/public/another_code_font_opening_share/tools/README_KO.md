# NDS 한글 폰트 분석 도구 모음

이 폴더는 《어나더 코드: 두 개의 기억》 한글화 후기에서 설명한 작업 원리를
다른 프로젝트에서도 시험해 볼 수 있도록 정리한 독립 실행형 예제입니다.

원본 ROM, 게임 데이터, 번역문, 상용 폰트는 포함하지 않습니다. 모든 명령은 반드시
사용자가 합법적으로 준비한 파일의 복사본을 대상으로 실행하세요.

## 포함 파일

| 파일 | 용도 |
|---|---|
| `01_extract_hangul_demand.py` | UTF-8 텍스트·CSV·JSON에서 완성형 한글의 등장 순서와 빈도를 추출 |
| `02_analyze_sjis_font.py` | 16×16 2bpp Shift-JIS형 폰트의 크기·빈 글리프를 분석하고 아틀라스 PNG 생성 |
| `03_audit_hangul_mapping.py` | 한글·2바이트 코드·글리프 번호의 중복과 계산식 불일치 검사 |
| `04_build_hangul_font.py` | 완성형 한글 PNG 시트의 글자를 매핑된 16×16 2bpp 글리프 칸에 삽입 |
| `05_mapping_to_tbl.py` | 구조화된 JSON 매핑을 일반적인 `XXXX=한` 형식의 `.tbl`로 변환 |
| `example_mapping.json` | 입력 형식을 보여 주는 여섯 글자 예제. 다른 게임에 그대로 사용 금지 |
| `requirements.txt` | PNG 처리를 위한 Pillow 의존성 |

## 준비

Python 3.10 이상을 권장합니다.

```powershell
python -m pip install -r requirements.txt
```

## 1. 번역문에서 필요한 한글 추출

```powershell
python 01_extract_hangul_demand.py translated.txt dialogue.csv `
  --output-json hangul_demand.json `
  --output-text hangul_demand.txt
```

`hangul_demand.txt`는 한글이 처음 등장한 순서이며, JSON에는 글자별 빈도와 최초
입력 파일이 들어갑니다. CSV의 메모 열까지 함께 읽히므로 실제 빌드 열만 따로 내보낸
파일을 입력하는 것이 가장 정확합니다.

## 2. 원본 폰트 분석

```powershell
python 02_analyze_sjis_font.py font.bin `
  --report font_report.json `
  --atlas font_atlas.png `
  --columns 64 `
  --scale 2
```

기본 전제는 16×16, 2bpp, 글리프당 64바이트, 8×8 타일 순서
`좌상→우상→좌하→우하`입니다. 대상 게임의 형식이 다르면 이 스크립트를 그대로
사용하면 안 됩니다.

## 3. 문자 매핑 검사

```powershell
python 03_audit_hangul_mapping.py example_mapping.json `
  --font font.bin `
  --report mapping_audit.json
```

다음을 검사합니다.

- 완성형 한글 한 글자인지
- 코드와 글리프 번호가 중복되지 않는지
- Shift-JIS형 계산식에서 코드와 글리프 번호가 서로 일치하는지
- 글리프가 폰트 파일 범위를 넘지 않는지
- 선택한 원본 글리프가 비어 있는지 또는 데이터가 있는지

## 4. 한글 글리프 삽입

```powershell
python 04_build_hangul_font.py `
  --base-font font.bin `
  --hangul-sheet hangul_16x16.png `
  --mapping my_mapping.json `
  --output font_ko.bin `
  --report font_build_report.json
```

한글 시트는 셀당 16×16, 64열이며 `가`부터 `힣`까지 유니코드 순서로 들어 있다고
가정합니다. 원본 폰트는 절대 덮어쓰지 않으며 출력 파일은 원본과 다른 경로여야 합니다.

## 5. `.tbl` 파일 만들기

```powershell
python 05_mapping_to_tbl.py my_mapping.json korean.tbl
```

출력 예:

```text
885C=한
84EA=글
86AB=뒤
984B=로
```

## 매우 중요한 제한

이 도구는 다른 게임에서 안전한 코드 영역을 자동으로 찾아 주지 않습니다. 빈 글리프,
원문에 안 나오는 코드, CP932 미정의 코드는 모두 후보일 뿐입니다. 대상 ROM 전체,
스크립트, 압축 리소스, 제어 코드와 실제 렌더러를 조사한 뒤 코드를 배정해야 합니다.

가장 먼저 코드 한 개와 글리프 한 개만 바꾸는 PoC를 만들고, 인접 문자와 게임 진행이
정상인지 확인하세요. 폰트 파일이 여러 개이거나 런타임 전환이 있는지도 별도로 조사해야
합니다.

## 라이선스

이 폴더의 예제 스크립트는 `LICENSE.txt`의 MIT License로 배포합니다. 게임과 폰트의
저작권은 각 권리자에게 있습니다.
