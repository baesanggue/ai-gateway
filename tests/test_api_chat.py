"""/v1/chat/completions 엔드포인트 검사 (OpenAI 호환 형식)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gateway.config import load_settings
from gateway.main import create_app

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(tmp_path):
    settings = load_settings(ROOT / "config.yaml", env_file=tmp_path / "없는.env")
    return TestClient(create_app(settings))


def post(client, **body):
    body.setdefault("model", "gpt-test")
    return client.post("/v1/chat/completions", json=body)


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_returns_openai_shaped_completion(client):
    r = post(client, messages=[{"role": "user", "content": "주문한 거 배송 언제 와요?"}])
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["id"].startswith("chatcmpl-")
    assert body["model"] == "gpt-test"
    choice = body["choices"][0]
    assert choice["message"]["role"] == "assistant"
    assert choice["message"]["content"]
    assert choice["finish_reason"] == "stop"
    assert set(body["usage"]) == {"prompt_tokens", "completion_tokens", "total_tokens"}


def test_accepts_text_parts_and_unknown_fields(client):
    r = post(client,
             messages=[{"role": "system", "content": "친절하게"},
                       {"role": "user", "content": [{"type": "text", "text": "안녕하세요"}]}],
             temperature=0.2, max_completion_tokens=50, top_p=1, seed=7)  # top_p·seed는 스키마에 없는 필드
    assert r.status_code == 200


def test_stream_is_rejected_with_openai_error(client):
    r = post(client, messages=[{"role": "user", "content": "안녕"}], stream=True)
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["param"] == "stream"
    assert err["type"] == "invalid_request_error"


def test_non_text_content_is_rejected(client):
    r = post(client, messages=[{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}]}])
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "unsupported_content"


@pytest.mark.parametrize("body", [
    {"messages": []},                                   # 메시지 없음
    {"messages": [{"role": "robot", "content": "x"}]},  # 없는 역할
    {"model": None, "messages": [{"role": "user", "content": "x"}]},
])
def test_invalid_requests_return_400_openai_error(client, body):
    r = client.post("/v1/chat/completions", json={"model": "gpt-test", **body})
    assert r.status_code == 400
    assert "error" in r.json() and r.json()["error"]["message"]
