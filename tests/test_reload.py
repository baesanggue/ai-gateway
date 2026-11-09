"""설정 다시 읽기 (/admin/reload, D26, #78): 화면에서 바꾼 설정을 다시 시작하지 않고 다음 요청부터 쓴다."""
import httpx
import pytest
from fastapi.testclient import TestClient

import gateway.main as main
from gateway.main import create_app
from gateway.storage.log import RequestLog
from tests.fakes import FakeUpstream, noul, test_settings

REMOTE = ("203.0.113.5", 50000)          # 문서용 예시 주소 (다른 PC)
CONFIDENTIAL = "다음 분기 인수 대상 회사 목록 정리해 줘"
BODY = {"model": "gpt-test", "messages": [{"role": "user", "content": CONFIDENTIAL}]}


def make_app(tmp_path, upstream, reload_settings, extra_env=""):
    return create_app(test_settings(tmp_path, extra_env), transport=httpx.MockTransport(upstream),
                      log=RequestLog(tmp_path / "gateway.db"), reload_settings=reload_settings)


def strict_settings(tmp_path):
    s = test_settings(tmp_path)
    s.config.security_level = "strict"
    return s


def test_reload_applies_to_the_next_request(tmp_path):
    up = FakeUpstream(local={"is_confidential": noul(0.9)})        # 기밀로 판단
    with TestClient(make_app(tmp_path, up, lambda: strict_settings(tmp_path))) as c:
        before = c.post("/v1/chat/completions", json=BODY).json()
        assert before["choices"][0]["finish_reason"] == "stop"      # 기본 수준: 기밀이어도 가명화해서 보낸다
        r = c.post("/admin/reload")
        assert r.status_code == 200 and r.json()["security_level"] == "strict"
        assert c.get("/healthz").json()["security_level"] == "strict"
        after = c.post("/v1/chat/completions", json=BODY).json()
    assert after["choices"][0]["finish_reason"] == "content_filter"  # 엄격 수준: 기밀이면 막는다
    assert "기밀" in after["choices"][0]["message"]["content"]


def test_failed_reload_keeps_current_settings(tmp_path):
    def broken():
        raise ValueError("업종 프리셋 'nope'이(가) 없다")

    with TestClient(make_app(tmp_path, FakeUpstream(), broken)) as c:
        r = c.post("/admin/reload")
        assert r.status_code == 400 and "nope" in r.json()["message"]
        assert c.get("/healthz").json()["security_level"] == "basic"
        assert c.post("/v1/chat/completions", json=BODY).status_code == 200


@pytest.mark.parametrize("client, headers, env", [
    (REMOTE, {}, ""),                                               # 다른 PC
    (REMOTE, {"Authorization": "Bearer gw-key-1"}, "GATEWAY_API_KEYS=gw-key-1\n"),   # 키가 맞아도 외부는 안 된다
    (("127.0.0.1", 50000), {"Origin": "http://evil.example"}, ""),  # 같은 PC 브라우저의 웹페이지
    (("127.0.0.1", 50000), {"Host": "evil.example:8000"}, ""),      # DNS 리바인딩
], ids=["remote", "remote-with-key", "web-page", "rebinding"])
def test_reload_only_from_programs_on_this_pc(tmp_path, client, headers, env):
    calls = []

    def loader():
        calls.append(1)
        return strict_settings(tmp_path)

    with TestClient(make_app(tmp_path, FakeUpstream(), loader, env), client=client) as c:
        r = c.post("/admin/reload", headers=headers)
    assert r.status_code == 403
    assert calls == []                                              # 설정 파일도 읽지 않는다


def test_previous_gateway_is_closed_later_not_immediately(tmp_path, monkeypatch):
    """처리 중인 요청이 이전 연결을 쓰고 있을 수 있어, 바로 닫지 않고 잠시 뒤에 닫는다."""
    scheduled = []

    class FakeTimer:
        def __init__(self, seconds, fn):
            scheduled.append((seconds, fn))

        def start(self):
            pass

    monkeypatch.setattr(main.threading, "Timer", FakeTimer)
    app = make_app(tmp_path, FakeUpstream(), lambda: strict_settings(tmp_path))
    with TestClient(app) as c:
        old = app.state.gateway
        c.post("/admin/reload")
        assert app.state.gateway is not old
    assert scheduled == [(main.RETIRE_SECONDS, old.close)]
