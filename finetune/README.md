# finetune/ — 파인튜닝 비교군 (D15, #20~#22)

현재 구성(로컬 gliclass + 클라우드 Decisions)과 비교할 **파인튜닝한 Laya(판단까지 전부 로컬)**를 만드는 곳이다.
프로젝트 개발을 우선하고, 남는 시간에만 진행한다 (`docs/decisions.md` D15).

| 단계 | 이슈 | 상태 |
|---|---|---|
| ① laya-serve로 로컬 가중치 서빙 확인 | #20 | 통과 (10/29) → `docs/laya-serve-notes.md` |
| ② 한국어 학습 데이터 생성 | #21 | 대기 |
| ③ 학습과 현재 구성과의 비교 | #22 | 대기 |

## 폴더

| 경로 | 내용 | git |
|---|---|---|
| `finetune/.venv/` | PyTorch·laya 전용 가상환경 (게이트웨이 의존성과 분리) | 제외 (`.venv/`) |
| `finetune/models/` | 내려받은 체크포인트, 학습 결과물 | 제외 (`models/`) |

설치·실행 방법은 `docs/laya-serve-notes.md`를 본다.
