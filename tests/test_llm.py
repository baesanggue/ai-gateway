"""LLM 클라이언트 검사 (#9). 실제 OpenAI 대신 httpx.MockTransport로 응답을 흉내 낸다."""
import json
from pathlib import Path

import httpx
import pytest

from gateway.config import load_settings
from gateway.llm.client import LLMClient, LLMError

ROOT = Path(__file__).resolve().parent.parent
TEST_KEY = "sk-test-only-not-a-real-key"
MASKED = [{"role": "user", "content": "[고객1] 고객([전화번호1])에게 배송 지연 사과 메일 써줘"}]


def completion(content="[고객1] 고객님, 배송이 늦어져 죄송합니다.", reasoning=0, **message):
    return {"id": "chatcmpl-x", "object": "chat.completion", "model": "upstream-name",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content, **message}}],
            "usage": {"prompt_tokens": 28, "completion_tokens": 115, "total_tokens": 143,
                      "completion_tokens_details": {"reasoning_tokens": reasoning}}}


class Recorder:
    def __init__(self, response):
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        return self.response(request) if callable(self.response) else self.response

    def body(self, i=0):
        return json.loads(self.requests[i].content)


def make_client(tmp_path, response, key=TEST_KEY):
    env = tmp_path / ".env"
    env.write_text(f"OPENAI_API_KEY={key}\n", encoding="utf-8")
    rec = Recorder(response)
    client = LLMClient.from_settings(load_settings(ROOT / "config.yaml", env_file=env),
                                     transport=httpx.MockTransport(rec))
    return client, rec


@pytest.mark.parametrize("tier, model, effort", [("cheap", "gpt-6-luna", "none"),
                                                 ("premium", "gpt-6.1-sol", "low")])
def test_tier_selects_model_and_reasoning_effort(tmp_path, tier, model, effort):
    client, rec = make_client(tmp_path, httpx.Response(200, json=completion(reasoning=21)))
    r = client.complete(tier, MASKED)

    req = rec.requests[0]
    assert (req.url.host, req.url.path) == ("api.openai.com", "/v1/chat/completions")
    assert req.headers["Authorization"] == f"Bearer {TEST_KEY}"
    assert rec.body() == {"model": model, "reasoning_effort": effort, "messages": MASKED}
    assert r.model == model and r.tier == tier          # 응답의 model이 아니라 요청한 모델
    assert (r.usage.prompt_tokens, r.usage.completion_tokens, r.usage.reasoning_tokens) == (28, 115, 21)
    assert r.text.startswith("[고객1]") and r.finish_reason == "stop" and r.refusal is None


def test_placeholder_instruction_is_prepended_only_for_masked_requests(tmp_path):
    client, rec = make_client(tmp_path, httpx.Response(200, json=completion()))
    client.complete("cheap", MASKED, masked=True)
    client.complete("cheap", MASKED, masked=False)

    first, second = rec.body(0)["messages"], rec.body(1)["messages"]
    assert first[0]["role"] == "developer" and "[고객1]" in first[0]["content"]
    assert first[1:] == MASKED
    assert second == MASKED


def test_max_completion_tokens_is_forwarded(tmp_path):
    client, rec = make_client(tmp_path, httpx.Response(200, json=completion()))
    client.complete("cheap", MASKED, max_completion_tokens=200)
    assert rec.body()["max_completion_tokens"] == 200


def test_model_refusal_is_returned_with_empty_text(tmp_path):
    client, _ = make_client(tmp_path, httpx.Response(200, json=completion(content=None, refusal="도와드릴 수 없습니다.")))
    r = client.complete("cheap", MASKED)
    assert r.text == "" and r.refusal == "도와드릴 수 없습니다."


def raise_timeout(request):
    raise httpx.ReadTimeout("timed out", request=request)


@pytest.mark.parametrize("response, expected", [
    (raise_timeout, "ReadTimeout"),
    # OpenAI 401 본문에는 키 일부가 들어 있다 → 메시지에는 code만 남긴다
    (httpx.Response(401, json={"error": {"message": f"Incorrect API key provided: {TEST_KEY[:8]}****",
                                         "code": "invalid_api_key"}}), "HTTP 401 code=invalid_api_key"),
    (httpx.Response(200, json={"unexpected": True}), "알 수 없는 응답 형식"),
], ids=["timeout", "http-401", "unknown-format"])
def test_errors_raise_llm_error_without_key_or_text(tmp_path, response, expected):
    client, _ = make_client(tmp_path, response)
    with pytest.raises(LLMError) as e:
        client.complete("cheap", MASKED)
    msg = str(e.value)
    assert expected in msg
    assert TEST_KEY[:8] not in msg and "배송 지연" not in msg


def test_client_refuses_to_start_without_api_key(tmp_path):
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        make_client(tmp_path, httpx.Response(200, json=completion()), key="")
