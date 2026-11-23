"""완전 로컬 수준 (#136, D34): 밖으로 보낼 수 없어서 막던 요청을 이 PC의 로컬 LLM(Ollama)이 원문으로 답한다.

- 원문은 로컬 LLM(127.0.0.1:11434)으로만 가고, OpenAI로는 가명화된 판단 요청만 간다(기밀이면 그것도 안 간다)
- 로컬 LLM이 꺼져 있으면 막고 안내한다. 원문을 클라우드로 보내는 대체 경로는 없다
- 위험·무관 같은 다른 사유가 섞이면 그대로 막는다. 기본·엄격 수준은 바뀌지 않는다
상위 서버(판단·LLM·Ollama)는 가짜(tests/fakes.py)다.
"""
import json
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gateway.config import LocalLLMConfig
from gateway.llm.client import LocalLLMClient
from gateway.main import create_app
from gateway.routing.policy import Decision, route_locally
from gateway.storage.log import RequestLog
from tests.fakes import TEST_KEY, FakeMasker, FakeUpstream, completion, noul, predicate, test_settings

AGE = "이 주민번호 900101-1234567로 고객 나이 계산해줘"      # 원본 값 필요 (가상 번호)
RAW = "900101-1234567"
SECRET = "다음 분기 인수 대상 회사 목록을 표로 정리해줘"        # 기밀로 판단되게 가짜 판단을 준다
NEEDS_RAW = {"needs_raw_value": predicate("needs_raw_value", 0.99)}


def run(tmp_path, upstream, text, level="local", **extra):
    settings = test_settings(tmp_path)
    settings.config.security_level = level
    log = RequestLog(tmp_path / "gateway.db")
    app = create_app(settings, transport=httpx.MockTransport(upstream), masker=FakeMasker(), log=log)
    with TestClient(app) as c:
        r = c.post("/v1/chat/completions", json={"model": "gpt-test", "messages": [{"role": "user", "content": text}],
                                                 **extra})
    row = log.get(r.headers["x-gateway-request-id"]) if "x-gateway-request-id" in r.headers else None
    return r, row, settings


def openai_chat_calls(upstream):
    return [r for r in upstream.requests if r.url.host == "api.openai.com" and r.url.path == "/v1/chat/completions"]


def test_raw_value_request_is_answered_by_the_local_llm_with_the_original_text(tmp_path):
    upstream = FakeUpstream(cloud=NEEDS_RAW, local_reply="1990년 1월 1일생이라 만 36세입니다.")
    r, row, _ = run(tmp_path, upstream, AGE)
    body = r.json()
    assert r.status_code == 200
    assert body["choices"][0]["message"]["content"] == "1990년 1월 1일생이라 만 36세입니다."
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["model"] == "gemma4:12b"
    # 로컬 LLM에는 원문이 간다 (이 PC 안). OpenAI 키는 보내지 않는다
    [local] = upstream.local_llm_requests
    sent = json.loads(local.content)
    assert sent["model"] == "gemma4:12b" and sent["reasoning_effort"] == "none"
    assert sent["messages"][0]["role"] == "system" and "오늘 날짜는" in sent["messages"][0]["content"]
    assert RAW in sent["messages"][-1]["content"]
    assert local.headers["authorization"] == "Bearer ollama" and TEST_KEY not in local.headers["authorization"]
    # 밖으로는 가명화된 판단 요청만 나갔고, OpenAI LLM은 부르지 않았다
    assert RAW not in upstream.raw_bytes().decode("utf-8")
    assert openai_chat_calls(upstream) == []
    # 요청 기록: 가명화된 문장만, 경로 local, 비용 0 (절감액에도 넣지 않는다)
    assert row["route"] == "local" and row["reasons"] == ["needs_raw"]
    assert row["tier"] == "local" and row["model"] == "gemma4:12b"
    assert row["cost_usd"] == 0 and row["baseline_cost_usd"] == 0
    assert RAW not in (row["text_masked"] or "")


def test_confidential_request_never_leaves_the_pc(tmp_path):
    upstream = FakeUpstream(local={"is_confidential": noul(0.99)})
    r, row, _ = run(tmp_path, upstream, SECRET)
    assert r.json()["choices"][0]["message"]["content"] == "이 PC에서 답했습니다."
    assert upstream.external_calls == 0                       # 클라우드 판단도 부르지 않는다
    assert len(upstream.local_llm_requests) == 1
    assert row["route"] == "local" and row["reasons"] == ["confidential"]


def test_local_llm_down_is_blocked_with_a_notice_and_nothing_raw_goes_out(tmp_path):
    upstream = FakeUpstream(cloud=NEEDS_RAW, local_llm_error=True)
    r, row, settings = run(tmp_path, upstream, AGE)
    body = r.json()
    assert r.status_code == 200                               # 오류(502)가 아니라 막고 안내한다
    assert body["choices"][0]["finish_reason"] == "content_filter"
    assert body["choices"][0]["message"]["content"] == settings.config.routing.block_messages["local_llm_error"]
    assert openai_chat_calls(upstream) == [] and RAW not in upstream.raw_bytes().decode("utf-8")
    assert row["route"] == "block" and row["reasons"] == ["local_llm_error", "needs_raw"]
    assert "ConnectError" in row["llm_error"]


def test_local_llm_down_with_stream_is_also_a_notice(tmp_path):
    upstream = FakeUpstream(cloud=NEEDS_RAW, local_llm_error=True)
    r, _, _ = run(tmp_path, upstream, AGE, stream=True)
    assert r.status_code == 200 and '"finish_reason": "content_filter"' in r.text


def test_risky_request_stays_blocked_even_if_it_needs_raw_values(tmp_path):
    upstream = FakeUpstream(cloud={**NEEDS_RAW, "is_risky": predicate("is_risky", 0.99)})
    r, row, settings = run(tmp_path, upstream, AGE)
    assert r.json()["choices"][0]["message"]["content"] == settings.config.routing.block_messages["risky"]
    assert upstream.local_llm_requests == []
    assert row["route"] == "block"


def test_simple_request_still_goes_out_masked_in_local_level(tmp_path):
    """완전 로컬이라고 모든 요청을 로컬로 보내지 않는다. 막을 이유가 없으면 지금처럼 가려서 클라우드로."""
    upstream = FakeUpstream(reply="[고객1] 고객님, 배송이 늦어져 죄송합니다.")
    r, row, _ = run(tmp_path, upstream, "김민수 고객(010-1234-5678)에게 배송 지연 사과 메일 써줘")
    assert r.json()["choices"][0]["message"]["content"] == "김민수 고객님, 배송이 늦어져 죄송합니다."
    assert upstream.local_llm_requests == [] and row["route"] == "cheap"


@pytest.mark.parametrize("level", ["basic", "strict"])
def test_other_levels_still_block_raw_value_requests(tmp_path, level):
    upstream = FakeUpstream(cloud=NEEDS_RAW)
    r, row, _ = run(tmp_path, upstream, AGE, level=level)
    assert r.json()["choices"][0]["finish_reason"] == "content_filter"
    assert upstream.local_llm_requests == [] and row["route"] == "block"


def test_streaming_works_for_the_local_route(tmp_path):
    r, _, _ = run(tmp_path, FakeUpstream(cloud=NEEDS_RAW, local_reply="만 36세입니다."), AGE, stream=True)
    text = "".join(json.loads(b[6:])["choices"][0]["delta"].get("content", "")
                   for b in r.text.split("\n\n") if b.startswith("data: {") and json.loads(b[6:])["choices"])
    assert text == "만 36세입니다."


@pytest.mark.parametrize("reasons,level,route", [
    (["needs_raw"], "local", "local"),
    (["confidential"], "local", "local"),
    (["pii_residual"], "local", "local"),
    (["pii_unmasked"], "local", "local"),
    (["risky", "needs_raw"], "local", "block"),          # 위험이 섞이면 막는다
    (["needs_raw", "off_topic"], "local", "block"),      # 무관도
    (["local_error"], "local", "block"),                 # 판단 실패는 막는다
    (["cloud_error"], "local", "block"),
    (["needs_raw"], "strict", "block"),                  # 다른 수준은 그대로
    (["needs_raw"], "basic", "block"),
])
def test_route_locally_only_turns_cannot_send_out_blocks_into_local(reasons, level, route):
    assert route_locally(Decision("block", reasons, "안내"), level).route == route


def test_non_block_decisions_are_not_changed():
    d = Decision("cheap", ["simple"])
    assert route_locally(d, "local") is d


@pytest.mark.parametrize("url", ["http://10.0.0.5:11434/v1", "https://api.openai.com/v1", "http://ollama.example.com/v1"])
def test_local_llm_address_must_be_this_pc(url):
    with pytest.raises(ValidationError):
        LocalLLMConfig(base_url=url)


def test_local_client_puts_today_in_the_instruction_and_maps_max_tokens():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=completion("답"))

    cfg = LocalLLMConfig(instruction="오늘 날짜는 {today}이다.")
    client = LocalLLMClient(cfg, httpx.MockTransport(handler))
    result = client.complete([{"role": "user", "content": "질문"}], max_completion_tokens=50, today=date(2026, 1, 2))
    assert seen[0]["messages"][0] == {"role": "system", "content": "오늘 날짜는 2026년 1월 2일이다."}
    assert seen[0]["max_tokens"] == 50 and "max_completion_tokens" not in seen[0]
    assert result.tier == "local" and result.text == "답"
