# 판단 클라이언트 (`gateway/decision/`, #8)

요청 하나를 받으면 판단 모델에 질문을 보내고, 답을 기준값과 비교해 "예/아니오", "단순/복잡", "FAQ/업무/무관"으로 바꾼다.
경로(FAQ·저렴·고성능·차단)는 정하지 않는다. 그것은 라우팅(`gateway/routing/`, #10)이 한다.

## 파일

| 파일 | 역할 |
|---|---|
| `questions.py` | 질문 6개의 문장과 형식. 190행 측정에 쓴 문장과 같다. 문장을 바꾸면 기준값을 다시 잰다 |
| `engines.py` | 엔진 2종. `SystemOneEngine`(Ollaya·laya-serve·Jev 공통 `/v1/systemone`), `OpenAIDecisionsEngine`(`/v1/decisions` 어댑터). 답을 질문별 확률표로 바꾼다 |
| `service.py` | `DecisionService`. 질문을 엔진에 나눠 보내고, 기준값 적용, 거부된 질문 다시 묻기, 오류를 `error`로 돌려주기 |

## 흐름 (D12)

```
judge_local(원문)                      → 로컬 엔진: has_pii, is_confidential
   (가명화, 엄격 수준 차단 확인 — #10·M2)
judge_cloud(가명화문, has_masked_pii)  → 클라우드 엔진: needs_raw_value*, is_risky, difficulty, intent
                                         * 가명화된 개인정보가 있을 때만 묻는다
```

- 두 함수를 하나로 합치지 않는다. 클라우드 함수는 가명화된 문장만 받는다는 규칙이 함수 형태에 드러나고, 테스트(`test_cloud_receives_only_masked_text`)로 확인한다.
- 질문을 어느 엔진에 보낼지는 `config.yaml`의 `decision.local_questions`, `decision.cloud.questions`가 정한다.

## 판단 결과 (`Judgment`)

| 질문 | 확률표 | `value` |
|---|---|---|
| 예/아니오 4종 | `{"yes": P(예)}` | P(예) ≥ `thresholds[질문]` 이면 `True` |
| difficulty | `{"simple": p, "complex": p}` | P(복잡) ≥ `thresholds.difficulty_complex` 이면 `"complex"` |
| intent | 선택지 4개의 확률 | 가장 높은 선택지를 `INTENT_GROUP`으로 묶은 값 (`faq` / `work` / `off_topic`) |

- `confidence`는 응답 필드를 쓰지 않고 확률표로 직접 계산한다: (K·p_max − 1)/(K − 1). 서버마다 계산법이 다르기 때문이다 (`docs/laya-serve-notes.md`).
- 로그에는 응답의 `model`이 아니라 요청한 모델 이름을 남긴다.

## 판단 거부와 오류

| 상황 | 처리 | 이후 (#10) |
|---|---|---|
| 난이도·의도 질문을 거부 | 그 질문만 따로 한 번 더 묻는다 (`RETRY_ON_REFUSAL`) | 그래도 거부면 `refused`로 남고, `on_refusal` 적용 |
| 위험·원본 필요 질문을 거부 | 다시 묻지 않는다 | `on_refusal` 적용 |
| 시간 초과, 연결 실패, HTTP 오류, 알 수 없는 응답 형식 | 예외 대신 `JudgeResult.error`. 메시지에 문장·키를 넣지 않는다 | 클라우드는 `on_error_route`, 로컬은 #10에서 정함 |
| `.env`에 `OPENAI_API_KEY`가 없음 | `DecisionService.from_settings`가 시작할 때 `ValueError` | — |

- HTTP 연결은 엔진마다 `httpx.Client` 하나를 재사용한다. 다 쓰면 `DecisionService.close()`.

## OpenAI Decisions 형식 (공식 문서 확인, 2026-10-30)

출처: [Decisions 가이드](https://developers.openai.com/api/docs/guides/decisions.md), API 레퍼런스 `POST /v1/decisions`

- 요청: `model`(`gpt-6-luna`만 지원), `input`(문자열), `questions`(1~400개)
  - predicate `{type, name, instructions}` · choice `{…, choices: [{value, description}]}`(2~255개) · score `{…, levels: [{label, description}]}`(2~10단계, 낮은 단계부터)
- 응답: `answers`(질문 순서대로), `model`, `usage.input_tokens`
  - predicate `probability` · choice `probabilities: [{value, probability}]` · score `probabilities: [{value, label, probability}]`
  - **거부는 질문별**: `{type: "refusal", name}`. 이유 필드는 없다. 다른 질문은 정상으로 답할 수 있다
- 문서에 없는 것: HTTP 오류 코드와 오류 본문 형식, 사용량 제한, 권장 시간 제한, 다시 시도 방법, 거부를 다시 물으면 달라지는지
- 가격: 캐시되지 않은 입력 100만 토큰당 $0.10, 출력은 무료

`/v1/systemone` 형식은 `docs/ollaya-notes.md` 5~6장을 본다.
