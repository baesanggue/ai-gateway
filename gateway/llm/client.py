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
from datetime import date
from typing import Literal

import httpx

from gateway.config import LLMConfig, LocalLLMConfig, Settings

Tier = Literal["cheap", "premium", "local"]


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

        return _post(self._client, body, model.model, tier)


class LocalLLMClient:
    """완전 로컬 수준의 로컬 LLM (#136, D34): 밖으로 보낼 수 없는 요청을 이 PC의 Ollama가 **원문으로** 답한다.

    - Ollama의 OpenAI 호환 /v1/chat/completions (docs.ollama.com/api/openai-compatibility, 11/23 확인).
      키는 "필요하지만 무시"라서 문서 예시대로 'ollama'를 보낸다. **OpenAI 키를 보내지 않는다**
    - 주소는 이 PC만 (LocalLLMConfig 검사). 원문을 받기 때문이다
    - 맨 앞에 설정의 지시(instruction)를 붙이고, {today}는 코드가 오늘 날짜로 바꾼다(날짜 계산은 코드, 설계 결정 6번)
    - 답에 가명 토큰이 없으니 복원하지 않는다
    """

    def __init__(self, cfg: LocalLLMConfig, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        self._client = httpx.Client(base_url=cfg.base_url.rstrip("/"), timeout=cfg.timeout_seconds,
                                    transport=transport, headers={"Authorization": "Bearer ollama"})

    def close(self) -> None:
        self._client.close()

    def complete(self, messages: list[dict[str, str]], *, max_completion_tokens: int | None = None,
                 today: date | None = None) -> LLMResult:
        if self.cfg.instruction:
            d = today or date.today()
            text = self.cfg.instruction.replace("{today}", f"{d.year}년 {d.month}월 {d.day}일")
            messages = [{"role": "system", "content": text}, *messages]
        body: dict = {"model": self.cfg.model, "messages": messages}
        if self.cfg.reasoning_effort:
            body["reasoning_effort"] = self.cfg.reasoning_effort
        if max_completion_tokens:
            body["max_tokens"] = max_completion_tokens     # Ollama 문서의 지원 목록은 max_tokens (max_completion_tokens는 없음)
        return _post(self._client, body, self.cfg.model, "local")


def _post(client: httpx.Client, body: dict, model: str, tier: Tier) -> LLMResult:
    """OpenAI 형식 /chat/completions를 부르고 결과를 LLMResult로. 실패는 LLMError (요청 문장·오류 본문은 넣지 않는다)."""
    t0 = time.perf_counter()
    try:
        r = client.post("/chat/completions", json=body)
    except httpx.HTTPError as e:
        raise LLMError(f"{model}: {type(e).__name__}") from e
    if r.status_code != 200:
        raise LLMError(f"{model}: HTTP {r.status_code} {_error_code(r)}")
    try:
        res = r.json()
        choice = res["choices"][0]
        u = res["usage"]
        usage = LLMUsage(u["prompt_tokens"], u["completion_tokens"],
                         (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0))
        return LLMResult(text=choice["message"].get("content") or "",
                         finish_reason=choice.get("finish_reason", ""),
                         refusal=choice["message"].get("refusal"), usage=usage, tier=tier,
                         model=model, latency_ms=(time.perf_counter() - t0) * 1000)
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise LLMError(f"{model}: 알 수 없는 응답 형식 ({type(e).__name__})") from e


def _error_code(r: httpx.Response) -> str:
    try:
        err = r.json().get("error") or {}
    except ValueError:
        return ""
    return " ".join(f"{k}={err[k]}" for k in ("code", "param") if err.get(k))
