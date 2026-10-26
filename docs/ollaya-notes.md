# Ollaya · Laya 확인 노트

- 확인일: 2026-10-26 · 기준 버전: **Ollaya v0.18.0** (2026-10-25 릴리스)
- 출처
  - [github.com/ollaya-dev/ollaya](https://github.com/ollaya-dev/ollaya) README, `docs/api.md` (§1~§15), `install.ps1`
  - [huggingface.co/convaiinnovations/laya-multilingual](https://huggingface.co/convaiinnovations/laya-multilingual) 모델 카드
- 이 문서에 없는 형식·필드는 추측하지 않는다. 버전이 바뀌면 다시 확인한다.

## 1. 무엇인가

- **Ollaya**: 판단 모델을 로컬에서 실행하는 서버. "판단 모델용 Ollama". Apache-2.0.
- **판단 모델**은 글을 생성하지 않는다. `state`(텍스트·JSON) + 타입이 정해진 질문을 받아 **보정된 확률**을 한 번의 연산으로 돌려준다.
- `/v1/systemone`은 TypeSafe API와 **형식이 완전히 같다**(`typesafe-sdk` 0.7.1 기준). 그래서 base URL만 바꾸면 Ollaya(로컬) ↔ Jev(클라우드)를 전환할 수 있다. CLAUDE.md 설계 결정 5번이 그대로 성립한다.

## 2. 설치 (Windows)

```powershell
irm https://ollaya.dev/install.ps1 | iex
```

- 하는 일: GitHub 릴리스 zip 다운로드 → sha256 검증 → `%LOCALAPPDATA%\Programs\Ollaya`에 설치 → 사용자 PATH에 추가
- NVIDIA GPU(드라이버 R580 이상)가 있으면 GPU 팩(`ollaya-windows-amd64-cuda.zip`, 약 1.4GB)도 받는다. `OLLAYA_NO_CUDA=1`이면 CPU용만 받는다(약 50MB).
- 대안: 데스크톱 앱, Docker(`ghcr.io/ollaya-dev/ollaya`, GPU는 `:cuda`)
- 모델은 **API가 자동으로 받지 않는다**. `ollaya pull <모델>`을 먼저 실행해야 한다. 우리 설치 스크립트(M4)에 넣어야 한다.

## 3. 서버 · 포트 · 보안

| 항목 | 값 |
|---|---|
| 기본 주소 | `http://127.0.0.1:11435` (`OLLAYA_HOST`) |
| 인증 | 기본은 없음 (루프백만 받음). `OLLAYA_API_KEY`를 설정하면 `Authorization: Bearer <키>` 필요 |
| 로그 | `state`와 질문 텍스트는 **로그에 남기지 않는다** (§14). 우리 원칙 P2(원본 미저장)와 맞는다 |
| 모델 저장 위치 | `~/.ollaya/models` (`OLLAYA_MODELS`) |
| 모델 유지 시간 | 마지막 요청 후 5분 (`OLLAYA_KEEP_ALIVE`). `/v1/*`은 요청별 `keep_alive`를 받지 않으므로 환경변수로 조정 |
| 장치 | `OLLAYA_DEVICE`: `auto`(CUDA 있으면 GPU) / `cpu` / `cuda` |

## 4. 모델

| 태그 | 내용 |
|---|---|
| `laya` | 라우터. 문자 체계·언어를 보고 `laya:en` 또는 `laya:multilingual`을 고른다. 한글은 `laya:multilingual`로 간다 |
| `laya:multilingual` | mmBERT-base 기반 322M, 한국어 포함 100개 이상 언어, 질문당 1,024토큰. 가중치 약 678MB, Apache-2.0(상업 사용 가능) |

- **우리는 `laya:multilingual`을 직접 지정한다.** 라우터를 거치면 입력에 따라 답하는 모델이 바뀔 수 있다. 그러면 신뢰도 기준값이 모델마다 달라진다.
- 정밀도: CUDA GPU에서는 fp16, CPU에서는 fp32. GPU와 CPU의 답이 거의 같은 확률일 때(상위 두 개 차이 0.01 미만) 다를 수 있다(일치율 99.1~99.6%).
- 정확도가 부족할 때의 비교 후보(메모만): `decima`(다국어 mmBERT, 321M), `largha`(144M), `winnow:e4b`(4B, GPU 권장, 공식 추천 모델).

## 5. 요청 형식 — `POST /v1/systemone`

```json
{
  "model": "laya:multilingual",
  "state": "주문한 거 배송 언제 와요?",
  "questions": {
    "intent": {
      "type": "choice",
      "instructions": "고객이 원하는 것은 무엇인가?",
      "criteria": {"delivery": "배송 일정 문의", "refund": "환불·반품", "other": "그 밖의 것"}
    },
    "needs_raw_value": {"type": "noul", "instructions": "답하려면 개인정보의 실제 값이 필요한가?"}
  }
}
```

| 규칙 | 내용 |
|---|---|
| 헤더 | `Authorization: Bearer <아무 값>` (TypeSafe SDK는 빈 키를 거부하므로 `local` 등) |
| `questions` | 1~256개. id는 자유 문자열, 순서 유지 |
| `choice` | `criteria` 필수: `{라벨: 설명}`. 2~255개. **Laya는 20개 이하 권장** |
| `score` | `criteria` 필수: 레벨 설명 배열(0부터). 2~10단계 |
| `noul` | 예/아니오. `criteria`는 선택(`{"true": ..., "false": ...}`) |
| `instructions` 생략 시 | 질문 id를 대신 읽는다. 그래서 id도 의미 있게 짓는다 |
| `state` 길이 | 최대 65,536토큰. `/v1/*`에서 모델 컨텍스트를 넘으면 잘라서 답하지 않고 `422 STATE_TRUNCATED` |

## 6. 응답 형식과 신뢰도

```json
{
  "model": "laya:multilingual",
  "answers": {
    "intent": {"type": "choice", "choice": "delivery", "confidence": 0.8995,
               "probabilities": {"delivery": 0.933, "refund": 0.021, "other": 0.046}},
    "needs_raw_value": {"type": "noul", "noul": 0.0412}
  },
  "usage": {"input_tokens": 71, "output_tokens": 0}
}
```
(형식은 api.md §8.1 예시, 값은 설명용)

| 타입 | 응답 필드 |
|---|---|
| `choice` | `choice`(최고 확률 라벨), `confidence`, `probabilities` |
| `score` | `score`(기댓값 Σ i·pᵢ, 소수 가능), `confidence`, `legend`, `probabilities` |
| `noul` | `noul`(참일 확률)만. **`confidence` 없음** |

- **`confidence` = (K·p_max − 1) / (K − 1)**, 범위 0~1. 모든 선택지가 같으면 0, 하나에 몰리면 1.
- **`noul`의 신뢰도는 우리 코드에서 계산한다**: 같은 공식에 K=2를 넣으면 `|2p − 1|`. (CLAUDE.md 원칙: 계산은 코드에서)
- 숫자는 소수 넷째 자리까지 반올림.
- 공식 문서 경고: **신뢰도 기준값은 모델마다 따로 맞춰야 한다**(다른 모델로 옮기면 의미가 달라짐).
- 판단 지연 측정: 네이티브 `POST /api/decide`는 같은 요청에 `total_duration`, `load_duration`, `eval_duration`(나노초)을 더해 준다. 벤치마크에는 이것을 쓴다.

## 7. ⚠️ 프로젝트에 영향을 주는 한계 (모델 카드)

1. **한국어 정확도는 중간 수준**: MASSIVE 의도 분류(20지선다)에서 한국어 0.490 (영어 전용 체크포인트는 0.110). 우리 데이터로 직접 재야 한다 → #5
2. **보정이 안 된 채 배포됨**: 평균 신뢰도 0.75~0.83인데 실제 정확도는 그보다 낮다(과신). 질문 타입·선택지 수별로 temperature를 다시 맞추면 보정 오차(ECE)가 0.314 → 0.106. → **신뢰도 기준값을 그대로 믿으면 안 된다.** M3에서 우리 테스트셋으로 temperature 보정 검토. 파인튜닝보다 훨씬 가볍다.
3. **`score`가 가장 약하고 위치 편향이 있음**: 첫 번째 레벨을 거의 고르지 않는다. → 난이도를 `score` 대신 `choice`(쉬움/보통/어려움)로 묻는 방식과 비교해야 한다.
4. **`noul`이 "참"을 낮게 보고할 수 있음**: 명백한 양성에도 P(참)≈0.5가 나온 사례가 있다. → 예/아니오 질문은 `noul`과 2지선다 `choice`를 비교해야 한다.
5. **질문 문장에 민감함**: 특정 업무 흐름에 대한 제로샷은 거의 무작위 수준이다. 질문(`instructions`)과 선택지 설명을 구체적으로 써야 한다.
6. `choice` 선택지는 20개 이하로 둔다.

## 8. #5 한국어 간이 테스트에 반영할 것

- 같은 질문을 `noul` vs 2지선다 `choice`로, 난이도를 `score` vs `choice`로 나란히 비교한다.
- 측정: 질문별 정확도, 평균 신뢰도(과신 정도), 판단 지연(`eval_duration`).
- 20문장은 테스트셋 범주에서 고르게 뽑는다 (단순 4 · 복잡 4 · 민감정보 4 · 원본 필요 4 · 기밀·위험·무관 4).

## 9. 남은 질문

- TypeSafe Python SDK를 쓸지, httpx로 직접 호출할지 → M1 #8에서 결정. 형식이 같으므로 httpx 직접 호출로도 충분하다.
- Ollama 0.40도 판단 모델 9종을 같은 형식으로 서빙한다(확률은 보정 없는 raw softmax). 참고만 한다.
