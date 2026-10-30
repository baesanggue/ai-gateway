"""LLM 클라이언트: 라우팅이 고른 등급(cheap | premium)의 모델로 답변을 생성한다 (OpenAI Chat Completions).

- 모델·추론 강도·가격은 config.yaml의 llm. 키는 .env의 OPENAI_API_KEY
- 받는 메시지는 가명화된 문장이어야 한다 (M2). 응답 복원은 masking이 한다
- 가명화된 요청이면 "가명 토큰을 그대로 써라"는 지시(llm.placeholder_instruction)를 맨 앞에 붙인다.
  붙이지 않으면 모델이 [고객1]을 "고객님"으로 바꿔 복원할 자리가 사라진다 (D17)
- 클라이언트가 보낸 temperature 같은 샘플링 값은 전달하지 않는다. 추론 모델이 받는지 확인하지 못했고,
  OpenAI가 400을 내면 "base URL만 바꾸면 동작"이 깨진다 (docs/llm-client.md)
"""
import time
from dataclasses import dataclass
from typing import Literal

import httpx

from gateway.config import LLMConfig, Settings

Tier = Literal["cheap", "premium"]


@dataclass
class LLMUsage:
    prompt_tokens: int
    completion_tokens: int   # 추론 토큰 포함 (출력 가격으로 청구)
    reasoning_tokens: int = 0


@dataclass
class LLMResult:
    text: str
    finish_reason: str       # stop | length | content_filter ...
    refusal: str | None      # 모델이 답변을 거부하면 그 안내문 (text는 빈 문자열)
    usage: LLMUsage
    tier: Tier
    model: str               # 요청한 모델 이름
    latency_ms: float


class LLMError(Exception):
    """LLM이 응답하지 않거나 오류를 돌려줌. 메시지에 요청 문장·키·오류 본문을 넣지 않는다
    (OpenAI 401 오류 본문에는 키 일부가 들어 있다). 오류 code와 param만 남긴다."""


class LLMClient:
    def __init__(self, cfg: LLMConfig, api_key: str, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        self._client = httpx.Client(base_url=cfg.base_url.rstrip("/"), timeout=cfg.timeout_seconds,
                                    transport=transport, headers={"Authorization": f"Bearer {api_key}"})

    @classmethod
    def from_settings(cls, settings: Settings, transport=None) -> "LLMClient":
        key = settings.secrets.openai_api_key
        if key is None or not key.get_secret_value():
            raise ValueError(".env에 OPENAI_API_KEY가 없다 (LLM 답변 생성에 필요)")
        return cls(settings.config.llm, key.get_secret_value(), transport)

    def close(self) -> None:
        self._client.close()

    def complete(self, tier: Tier, messages: list[dict[str, str]], *, masked: bool = False,
                 max_completion_tokens: int | None = None) -> LLMResult:
        model = self.cfg.cheap if tier == "cheap" else self.cfg.premium
        if masked and self.cfg.placeholder_instruction:
            messages = [{"role": "developer", "content": self.cfg.placeholder_instruction}, *messages]
        body: dict = {"model": model.model, "messages": messages}
        if model.reasoning_effort:
            body["reasoning_effort"] = model.reasoning_effort
        if max_completion_tokens:
            body["max_completion_tokens"] = max_completion_tokens

        t0 = time.perf_counter()
        try:
            r = self._client.post("/chat/completions", json=body)
        except httpx.HTTPError as e:
            raise LLMError(f"{model.model}: {type(e).__name__}") from e
        if r.status_code != 200:
            raise LLMError(f"{model.model}: HTTP {r.status_code} {_error_code(r)}")
        try:
            res = r.json()
            choice = res["choices"][0]
            u = res["usage"]
            usage = LLMUsage(u["prompt_tokens"], u["completion_tokens"],
                             (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0))
            return LLMResult(text=choice["message"].get("content") or "",
                             finish_reason=choice.get("finish_reason", ""),
                             refusal=choice["message"].get("refusal"), usage=usage, tier=tier,
                             model=model.model, latency_ms=(time.perf_counter() - t0) * 1000)
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise LLMError(f"{model.model}: 알 수 없는 응답 형식 ({type(e).__name__})") from e


def _error_code(r: httpx.Response) -> str:
    try:
        err = r.json().get("error") or {}
    except ValueError:
        return ""
    return " ".join(f"{k}={err[k]}" for k in ("code", "param") if err.get(k))
