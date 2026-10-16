# 최종 배포본 재빌드

최종 v44 빌드에 필요한 JSON 6개, 수정 그래픽 PNG 65개, 폰트 PNG 1개를 저장소에 포함했습니다. **이 저장소와 별도로 준비한 지원 일본판 원본 ROM만 있으면 최종판을 재빌드할 수 있습니다.** 개인 복원 ZIP은 필수가 아닙니다.

## 준비와 실행

Python 3.12를 설치하고 저장소 최상위에서 실행합니다.

```powershell
python -m pip install -r requirements-rebuild.txt
python scripts/rebuild_final.py --source-rom "보유한 원본 ROM의 절대 경로.nds" --output-dir build/final_v44
```

원본 ROM은 일본판 ANOJ이며 SHA-256은 아래 표와 일치해야 합니다. 원본을 수정하지 않습니다. 출력 폴더는 아직 존재하지 않는 경로여야 합니다. 공개 입력 72개와 번역 CSV의 해시를 확인한 뒤 텍스트·그래픽을 차례로 빌드합니다. 결과는 출력 폴더의 `AnotherCode_KR_Final_20260829.nds`이며 `REBUILD_RESULT.json`에 해시 검증 결과를 기록합니다.

| 단계 | SHA-256 |
| --- | --- |
| 지원 원본 | `6c8cb19de7b86dc5f1147e4927d6916571968a06b5c2e9b88f62c22c271dd0a1` |
| 텍스트 빌드 | `6584b95f445082d6df6caafbb1852cd0e5d4226b272e8512d28401284d4eed73` |
| 최종 ROM | `28b914e99a54b74576f0684a49898c89989ee92d2566d6683e6c1246b9711e9d` |

## 포함된 입력

- `workspace/text/source/`: 스크립트·보조 텍스트 후보 JSON 2개
- `workspace/analysis/`: 참조·번역 감사·재배치 용량·팩 자원 감사 JSON 4개
- `workspace/graphics/translate_final_v39_cumulative/indexed/`: 최종 수정 PNG 65개
- `inputs/font.png`: 선택한 Galmuri11 Condensed 비트맵 글리프 시트

분석 JSON에는 원문 텍스트와 오프셋 등 재삽입용 정보가 포함됩니다. 원본 ROM·BIOS·추출된 게임 바이너리와 출력 ROM은 포함하지 않습니다. 파일별 공개 검토 기록과 해시는 [PUBLIC_INPUTS_v44.json](PUBLIC_INPUTS_v44.json)에 있습니다. 폰트의 출처·이용 조건은 [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md)와 [Galmuri 라이선스](../../licenses/Galmuri-OFL-1.1.md)를 참고하세요.

이전 개인 복원 ZIP을 보유한 경우 `--source-rom` 대신 `--private-inputs "ZIP 경로"`도 사용할 수 있습니다. 이 ZIP에는 원본 ROM이 포함되어 있으므로 공개하지 마세요. `PRIVATE_INPUTS_v44.json`은 이전 ZIP과 현재 공개 입력의 동일성을 확인하는 해시 목록이며, 해당 파일 이름이 현재 입력 전체의 비공개 여부를 뜻하지는 않습니다.

## 수정 작업과 검증 범위

이 명령은 동결된 최종판 재현용입니다. 번역 CSV가 바뀌면 중단합니다. 새 번역에는 `audit_translation_csv.py`, `analyze_translation_relocation.py`, `analyze_full_hangul_code_pool.py`, `generate_full_hangul_mapping.py`로 해당 버전의 분석·매핑을 갱신하고 기존 하위 빌더에 새 경로를 넘겨 별도 버전을 만드세요. PNG는 위 공개 경로에서 편집할 수 있습니다. 상세 작업 이력은 `docs/PROJECT_NOTES.md`를 참고하세요.

앞선 두 번의 재빌드와 공개 릴리즈 패치 적용에서 최종 ROM 해시가 일치했습니다. 이번 공개 입력 방식의 실제 빌드 결과는 [PUBLIC_REBUILD_VERIFICATION_2026-10-16.json](PUBLIC_REBUILD_VERIFICATION_2026-10-16.json)에 기록합니다. 해시 검증과 에뮬레이터 실행·장면 검수·전체 플레이 검수는 별개이며 이번 작업에서 새 플레이 검수는 수행하지 않습니다.
