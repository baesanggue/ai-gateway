# LLM 클라이언트 (`gateway/llm/`, #9)

라우팅이 고른 등급(저렴한 모델 / 고성능 모델)으로 답변을 생성하고, 토큰 수로 비용을 계산한다.
**생성형 AI의 역할**: 가명화된 요청을 받아 답변 글을 쓴다. 판단(어느 모델로 보낼지)은 판단 모델이, 생성은 LLM이 맡는다.

## 파일

| 파일 | 역할 |
|---|---|
| `client.py` | `LLMClient.complete(tier, messages, masked=...)`. OpenAI `/v1/chat/completions`를 httpx로 부른다. 결과는 답변, 종료 이유, 모델 거부 문구, 토큰 수(추론 토큰 포함), 지연 |
| `cost.py` | `request_cost`(실제 비용), `baseline_cost`("전부 고성능 모델이었다면" 비용, 비용 카운터의 비교 기준) |

## 모델 (D17, `config.yaml`의 `llm`)

| 등급 | 모델 | 추론 강도 | 가격 (100만 토큰당 입력 / 출력) |
|---|---|---|---|
| cheap | `gpt-6-luna` | none | $0.10 / $0.50 |
| premium | `gpt-6.1-sol` | low (가장 낮은 값) | $2.00 / $10.00 |

## 요청을 만드는 규칙

- **가명 토큰 지시**: 가명화된 요청(`masked=True`)이면 `llm.placeholder_instruction`을 `developer` 메시지로 맨 앞에 붙인다. 붙이지 않으면 모델이 `[고객1]`을 "고객님"으로 바꿔 복원할 자리가 사라진다. 효과는 `bench/results/placeholder-2026-10-30/`에 있다.
- **전달하는 값**: `model`, `messages`, `reasoning_effort`(설정값), `max_completion_tokens`(클라이언트가 보낸 경우).
- **전달하지 않는 값**: `temperature` 같은 샘플링 값. GPT-6는 추론 모델이라 이 값을 받는지 문서에서 확인하지 못했다. 그대로 넘겼다가 OpenAI가 400을 내면 "base URL만 바꾸면 동작"이 깨진다.
- 로그에는 응답의 `model`이 아니라 요청한 모델 이름을 남긴다.

## 오류

| 상황 | 처리 |
|---|---|
| 시간 초과·연결 실패, HTTP 오류, 알 수 없는 응답 형식 | `LLMError`. 메시지에는 모델 이름, 상태 코드, OpenAI 오류의 `code`·`param`만 넣는다. OpenAI 401 본문에는 키 일부가 들어 있어서 본문은 넣지 않는다 |
| 모델이 답변을 거부 | 예외가 아니다. `text`는 빈 문자열, `refusal`에 거부 문구 |
| `.env`에 `OPENAI_API_KEY` 없음 | `LLMClient.from_settings`가 시작할 때 `ValueError` |

- `LLMError`를 OpenAI 형식의 오류 응답(502)으로 바꿔 돌려주는 일은 #12가 한다.
