# 요청 로그 (`gateway/storage/`, #11)

요청 1건마다 판단 과정과 비용을 SQLite에 한 줄로 남긴다. 관리 화면(M4)과 비용 카운터(시연 6번)가 이 로그를 읽는다.

**생성형 AI의 역할**: 로그 자체는 코드다. 판단 모델(로컬·클라우드)이 내린 판단과 LLM이 쓴 토큰을 기록해, 생성형 AI가 각 요청에서 무엇을 했는지 나중에 보여 준다.

## 무엇을 저장하나 (CLAUDE.md §5)

| 열 | 내용 |
|---|---|
| `id`, `created_at` | 요청 ID, 생성 시각(UTC) |
| `security_level` | basic / strict / local |
| `text_masked` | **가명화된** 요청 문장. 원본이 의심되면 `NULL` |
| `text_withheld` | 1이면 원본 의심으로 문장을 저장하지 않음 |
| `pii_counts` | 개인정보 종류·개수 JSON (`{"전화번호": 1, "고객": 1}`). **값은 없다** |
| `route`, `reasons`, `faq_id` | 경로, 사유 코드 전부(차단이면 걸린 사유 모두, #10), FAQ id |
| `judgments` | 로컬·클라우드 판단 JSON: 질문별 값·신뢰도·확률·**거부 여부**, 엔진 오류, 지연, 입력 토큰 (`judge_summary()`) |
| `tier`, `model` | LLM 등급과 요청한 모델 이름 |
| `prompt_tokens`, `completion_tokens`, `reasoning_tokens` | LLM 토큰 (추론 토큰은 출력에 포함) |
| `cost_usd`, `baseline_cost_usd` | 실제 비용, "전부 고성능이었다면" 비용 (D18) |
| `decision_input_tokens` | 클라우드 판단 입력 토큰. 판단 비용은 기존 방식에 없던 비용이라 따로 남긴다 |
| `decision_latency_ms`, `total_latency_ms` | 판단 지연, 전체 지연 |
| `llm_error` | LLM 오류 메시지 (모델·상태 코드·오류 code만 들어 있다, #9) |

- **저장하지 않는 것**: LLM 답변. 복원된 답변에는 원본 이름·번호가 들어 있다.

## 원본이 남지 않게 하는 장치 (설계 결정 2번)
1. 저장 함수는 `text_masked`, 즉 **가명화된 문장만** 받는다.
2. **안전장치**: 저장 직전에 `gateway/masking/patterns.py`의 정규식으로 형식 있는 원본(전화번호·주민번호·카드번호·계좌번호·이메일·주소)이 남아 있는지 한 번 더 본다.
   - 남아 있으면 문장을 저장하지 않고 `text_withheld=1`만 남긴다.
   - 로그 때문에 사용자 요청이 실패하면 안 되므로 예외는 내지 않는다.
   - 이름은 정규식으로 못 잡는다. 이 장치는 보조이고, 이름은 M2 가명화(사용자 사전)가 막는다.
3. **테스트**: 원본이 든 요청을 저장한 뒤 DB·WAL 파일의 **바이트 전체**에서 원본이 없음을 확인한다.
   - 안전장치를 끄면 원본이 파일에 남는 것도 따로 확인했다. 검사가 실제로 원본을 잡아낼 수 있다는 뜻이다.

## 사용
```python
from gateway.storage.log import RequestLog, RequestRecord, judge_summary

log = RequestLog.from_config(settings.config.storage)    # data/gateway.db
log.save(RequestRecord(security_level="basic", text_masked=masked, route=d.route, reasons=d.reasons,
                       judgments={"local": judge_summary(local), "cloud": judge_summary(cloud)}, ...))
log.recent(50)     # 관리 화면
log.totals()       # {"requests", "by_route", "cost_usd", "baseline_cost_usd", "saved_usd", ...}
```
- 요청 처리 흐름에 붙이는 일은 #12가 한다.

## 파일과 동시 접근
- `config.yaml`의 `storage.sqlite_path`(기본 `data/gateway.db`). 상대 경로는 저장소 루트 기준이다.
- **WAL 모드**: 게이트웨이(FastAPI는 요청을 여러 스레드에서 처리)가 쓰는 동안 관리 화면이 읽는다. 저장할 때마다 연결을 새로 열고 닫는다. 4스레드 × 25건 동시 저장을 테스트로 확인했다.
- WAL 모드는 DB 옆에 `gateway.db-wal`, `gateway.db-shm`을 만든다.
  - 기존 `.gitignore`의 `*.db`에는 걸리지 않아 `*.db-wal`·`*.db-shm`·`*.db-journal`을 추가했다.
  - `git check-ignore`로 네 파일 모두 제외되는 것을 확인했다.

## 정규식 보안 검토 (11/01)
안전장치가 쓰는 `gateway/masking/patterns.py`를 커밋한 뒤 자동 보안 검토가 두 가지를 지적했고, 직접 재서 확인한 뒤 고쳤다.

| 지적 | 확인 | 조치 |
|---|---|---|
| 정규식 과부하(ReDoS) | 측정용 사본의 주소·이메일 패턴이 공백 없는 2천 자 입력에서 100초 안에 끝나지 않음 | 반복 길이 상한, 주소 구간끼리 겹치지 않게 함 → 10만 자도 0.1초 안 |
| 하이픈 없는 개인정보 우회 | `01012345678`, 13자리 주민번호, 16자리 카드번호를 못 잡음 | 잡도록 고침. 숫자 경계로 주문번호·운송장 번호는 잡지 않음 |

- 남은 한계 (M2에서 다룬다)
  - 하이픈 없는 계좌번호는 다른 번호와 구분할 수 없다.
  - 주소 패턴이 "내년도 연구개발비로 30억"에도 걸린다. 안전장치에서는 문장을 빼는 쪽이라 안전하다.
