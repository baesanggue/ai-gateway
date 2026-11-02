"""게이트웨이 키 검사 (D20, #12 보안 검토). 회사의 OpenAI 키를 아무나 대신 쓰지 못하게 한다."""
import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from gateway.main import create_app
from gateway.storage.log import RequestLog
from tests.fakes import TEST_KEY, FakeUpstream, test_settings

REMOTE = ("203.0.113.5", 50000)          # 문서용 예시 주소 (다른 PC)
BODY = {"model": "gpt-test", "messages": [{"role": "user", "content": "회의 공지 짧게 써 줘"}]}


def make_app(tmp_path, upstream, gateway_keys=""):
    return create_app(test_settings(tmp_path, f"GATEWAY_API_KEYS={gateway_keys}\n"),
                      transport=httpx.MockTransport(upstream), log=RequestLog(tmp_path / "gateway.db"))


def test_without_keys_local_requests_need_nothing(tmp_path):
    """시연·개발: 같은 PC에서는 base URL만 바꾸면 된다 (설계 결정 4번)."""
    with TestClient(make_app(tmp_path, FakeUpstream())) as c:
        assert c.post("/v1/chat/completions", json=BODY).status_code == 200


def test_without_keys_remote_requests_are_refused_before_any_call(tmp_path):
    up = FakeUpstream()
    with TestClient(make_app(tmp_path, up), client=REMOTE) as c:
        r = c.post("/v1/chat/completions", json=BODY)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_api_key" and "GATEWAY_API_KEYS" in r.json()["error"]["message"]
    assert up.requests == []


@pytest.mark.parametrize("header, status", [
    (None, 401),
    ("Bearer wrong-key", 401),
    ("gw-key-1", 401),                   # Bearer 없이
    ("Bearer gw-key-1", 200),
    ("bearer gw-key-2", 200),            # 두 번째 키, 소문자 bearer
], ids=["missing", "wrong", "no-bearer", "key-1", "key-2"])
def test_with_keys_every_request_needs_a_valid_key(tmp_path, header, status):
    up = FakeUpstream()
    with TestClient(make_app(tmp_path, up, gateway_keys="gw-key-1, gw-key-2"), client=REMOTE) as c:
        r = c.post("/v1/chat/completions", json=BODY, headers={"Authorization": header} if header else {})
    assert r.status_code == status
    if status == 401:
        assert up.requests == []


def test_client_key_is_not_forwarded_upstream(tmp_path):
    """서비스가 보낸 게이트웨이 키는 OpenAI로 가지 않는다. 상위에는 회사 키만 간다."""
    up = FakeUpstream()
    with TestClient(make_app(tmp_path, up, gateway_keys="gw-key-1"), client=REMOTE) as c:
        c.post("/v1/chat/completions", json=BODY, headers={"Authorization": "Bearer gw-key-1"})
    sent = [r.headers.get("authorization", "") for r in up.requests if r.url.host == "api.openai.com"]
    assert sent and all(h == f"Bearer {TEST_KEY}" for h in sent)


def test_openai_sdk_uses_gateway_key_as_api_key(tmp_path):
    """외부에서 쓰는 서비스는 base URL과 api_key(게이트웨이 키)를 바꾼다. SDK는 api_key를 Bearer로 보낸다."""
    client = TestClient(make_app(tmp_path, FakeUpstream(reply="공지입니다."), gateway_keys="gw-key-1"), client=REMOTE)
    with client:
        sdk = openai.OpenAI(base_url="http://testserver/v1", api_key="gw-key-1", http_client=client, max_retries=0)
        assert sdk.chat.completions.create(**BODY).choices[0].message.content == "공지입니다."
        bad = openai.OpenAI(base_url="http://testserver/v1", api_key="nope", http_client=client, max_retries=0)
        with pytest.raises(openai.AuthenticationError):
            bad.chat.completions.create(**BODY)


# ── 같은 PC의 브라우저를 이용한 요청 (DNS 리바인딩·교차 사이트, #12 보안 검토) ──

@pytest.mark.parametrize("headers", [
    {"Host": "evil.example"},                                  # DNS 리바인딩: 접속은 127.0.0.1, Host는 공격자 도메인
    {"Origin": "https://evil.example"},                        # 악성 웹페이지에서 보낸 요청
    {"Host": "evil.example:8000", "Origin": "http://evil.example:8000"},
], ids=["rebinding-host", "foreign-origin", "both"])
def test_without_keys_browser_requests_from_other_sites_are_refused(tmp_path, headers):
    up = FakeUpstream()
    with TestClient(make_app(tmp_path, up)) as c:
        r = c.post("/v1/chat/completions", json=BODY, headers=headers)
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden_origin"
    assert up.requests == []


@pytest.mark.parametrize("headers", [{"Host": "127.0.0.1:8000"}, {"Host": "localhost:8000"}, {"Host": "[::1]:8000"},
                                     {"Origin": "http://localhost:8501"}],
                         ids=["ipv4", "localhost", "ipv6", "local-origin"])
def test_without_keys_local_hosts_and_origins_are_allowed(tmp_path, headers):
    with TestClient(make_app(tmp_path, FakeUpstream())) as c:
        assert c.post("/v1/chat/completions", json=BODY, headers=headers).status_code == 200


def test_text_plain_body_is_not_processed(tmp_path):
    """브라우저가 사전 확인(preflight) 없이 보낼 수 있는 text/plain 요청은 처리하지 않는다 (FastAPI가 JSON으로 읽지 않음)."""
    import json
    up = FakeUpstream()
    with TestClient(make_app(tmp_path, up)) as c:
        r = c.post("/v1/chat/completions", content=json.dumps(BODY), headers={"Content-Type": "text/plain"})
    assert r.status_code == 400 and up.requests == []


def test_with_keys_host_check_is_not_needed(tmp_path):
    """키 모드에서는 공격자 웹페이지가 키를 모르므로 Host·Origin으로 막지 않는다 (프록시 뒤 배포를 위해)."""
    with TestClient(make_app(tmp_path, FakeUpstream(), gateway_keys="gw-key-1"), client=REMOTE) as c:
        r = c.post("/v1/chat/completions", json=BODY,
                   headers={"Authorization": "Bearer gw-key-1", "Host": "gateway.example.com"})
    assert r.status_code == 200
