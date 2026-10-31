# finetune/ — 파인튜닝 비교군 (D15, #20~#22)

현재 구성(로컬 gliclass + 클라우드 Decisions)과 비교할 **파인튜닝한 Laya(판단까지 전부 로컬)**를 만드는 곳이다.
프로젝트 개발을 우선하고, 남는 시간에만 진행한다 (`docs/decisions.md` D15).

| 단계 | 이슈 | 상태 |
|---|---|---|
| ① laya-serve로 로컬 가중치 서빙 확인 | #20 | 통과 (10/29) → `docs/laya-serve-notes.md` |
| ② 한국어 학습 데이터 생성 | #21 | 진행 중: 생성 모델 `gemma4:12b` 결정(10/30), 생성기 `finetune/generate.py`와 시범 246문장·표본 검토(10/31, `finetune/data/pilot-2026-10-31/`) |
| ③ 학습과 현재 구성과의 비교 | #22 | 대기 |

## 폴더

| 경로 | 내용 | git |
|---|---|---|
| `finetune/.venv/` | PyTorch·laya 전용 가상환경 (게이트웨이 의존성과 분리) | 제외 (`.venv/`) |
| `finetune/models/` | 내려받은 체크포인트, 학습 결과물 | 제외 (`models/`) |
| `finetune/generate.py` | 학습 데이터 생성기 (gemma4:12b + 코드로 채운 가상 값). 게이트웨이 환경으로 실행: `uv run python finetune/generate.py --out-dir …` | 포함 |
| `finetune/data/` | 생성한 학습 데이터(`train.jsonl`, laya `{state, questions, expected}` 형식)와 표본 검토 결과. 가상 값만 들어 있다 | 포함 |

## 학습 데이터 (#21)

- **정답은 범주가 정한다.** 범주(`CATEGORIES`)가 확실히 정하는 판단에만 정답을 붙이고, 나머지 질문은 비워 둔다. laya는 정답이 없는 질문을 학습에서 뺀다.
- **개인정보 정답은 코드가 채운 값으로 정한다.** gemma4는 `{이름}`·`{전화번호}` 자리표시만 쓰고, 코드가 가상 값을 채운다.
- **가명화본**: 개인정보가 있는 문장은 `[고객1]` 토큰으로 바꾼 줄도 만든다. 이 줄의 정답은 "남은 개인정보 없음"이다.
- **품질 기준**: 무작위 50줄 직접 검토, 정답 오류 5% 이하 (`docs/llm-api-notes.md` §4)

설치·실행 방법은 `docs/laya-serve-notes.md`를 본다.
