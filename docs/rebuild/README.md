# 최종 배포본 복원·재빌드

2026-10-16 별도 Git 복제본과 개인 복원 입력 묶음으로 2026-08-29 최종판(v44)을 다시 만들었습니다. 최종 ROM SHA-256은 기존 릴리즈의 패치 적용 결과와 일치합니다.

GitHub 소스만으로는 부족합니다. **비공개 복원 입력 ZIP**도 필요합니다. 원본 게임·수정 그래픽·폰트·원문 분석 자료가 들어 있으므로 이 ZIP을 공개 저장소나 릴리즈에 올리지 마세요. 전체 작업 이력 백업과 복원 입력 ZIP은 별개이며 둘 다 보관합니다.

## 필요한 자료

- 이 저장소의 `main` 복제본
- `01_Another_Code_PRIVATE_REBUILD_INPUTS_20261016_174144.zip`
- Python 3.12 및 `requirements-rebuild.txt`의 Pillow 12.3.0

개인 입력 ZIP SHA-256: `8a7b44695526f211c079fcdeb5170509df214eb7f3db0b3fbca1d89777552a32`

ZIP 내 73개 파일: 지원 일본판 원본 ROM 1개, 선택된 Galmuri 폰트 PNG 1개, 동결된 분석 자료 6개, 최종 수정 그래픽 PNG 65개. 파일별 크기·SHA-256은 [PRIVATE_INPUTS_v44.json](PRIVATE_INPUTS_v44.json)에 있습니다. 이 공개 JSON에는 식별 정보만 들어 있으며 실제 게임 데이터는 없습니다.

## 실행

새 폴더에서 저장소를 복제한 뒤:

```powershell
python -m pip install -r requirements-rebuild.txt
python scripts/rebuild_final.py --private-inputs "개인 복원 입력 ZIP의 절대 경로" --output-dir build/restored_final_v44
```

출력 폴더는 새 경로여야 합니다. 기존 결과는 덮어쓰지 않습니다. 입력 ZIP의 전체 파일 해시와 현재 번역 원장 해시를 확인한 뒤 텍스트·그래픽을 차례로 빌드합니다.

결과는 `build/restored_final_v44/AnotherCode_KR_Final_20260829.nds`, 검증 결과는 `REBUILD_RESULT.json`입니다. 출력 폴더에 추출되는 `private_inputs/`도 비공개 자료입니다. 소스 ROM, 입력 ZIP, 출력 ROM을 Git에 추가하지 않습니다.

| 단계 | SHA-256 |
| --- | --- |
| 지원 원본 | `6c8cb19de7b86dc5f1147e4927d6916571968a06b5c2e9b88f62c22c271dd0a1` |
| 텍스트 빌드 | `6584b95f445082d6df6caafbb1852cd0e5d4226b272e8512d28401284d4eed73` |
| 최종 ROM | `28b914e99a54b74576f0684a49898c89989ee92d2566d6683e6c1246b9711e9d` |

## 작업을 이어갈 때

이 명령은 **동결된 최종판의 복원 확인용**입니다. 번역 CSV를 바꾸면 예전 감사·용량 분석·글리프 매핑과 맞지 않으므로 중단합니다. 변경 후에는 `audit_translation_csv.py`, `analyze_translation_relocation.py`, `analyze_full_hangul_code_pool.py`, `generate_full_hangul_mapping.py`로 새 입력을 생성하고 기존 하위 빌더에 새 경로를 넘겨 별도 버전을 만듭니다. 상세 명령과 그래픽 작업 이력은 `docs/PROJECT_NOTES.md` 및 보존된 전체 작업 백업을 참고합니다. 수정 그래픽을 편집할 때는 개인 입력의 PNG 또는 전체 백업의 편집 원본을 별도 작업 폴더에 복원합니다.

## 검증 범위

- 전체 백업 4,236개 파일 및 개인 복원 입력 73개 SHA-256 일치
- Git 복제본과 위 입력으로 텍스트·최종 ROM 해시 일치
- GitHub 릴리즈 ZIP 해시·CRC 및 동봉 `apply.ps1` 적용 결과 해시 일치
- 기존 unittest 72개 통과, 원본 재추출 바이너리 92개 해시 일치
- 이번 새 에뮬레이터 실행·장면 검수·전체 플레이는 미수행. emucap NDS 실행 계획에서 `adapter binary is unavailable`이 확인됐습니다. 해시 일치를 새 실행 검증으로 표기하지 않습니다.
