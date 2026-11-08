"""게이트웨이 벤치마크(#59)의 도구: 응답을 다시 쓰는 전송 계층, 원본 값 찾기, 엄격 수준 기대 경로. 네트워크 없이."""
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import run_gateway as rg  # noqa: E402


def test_replay_serves_recorded_response_without_calling_again():
    """엄격 수준 재실행은 기본 수준이 받은 응답을 그대로 쓴다. 같은 주소·본문이면 실제로 부르지 않는다."""
    calls = []

    def upstream(request):
        calls.append(request.content)
        return httpx.Response(200, json={"n": len(calls)}, headers={"content-encoding": "identity"})

    t = rg.ReplayTransport(httpx.MockTransport(upstream))
    client = httpx.Client(transport=t)
    assert client.post("http://x/v1/a", content=b"same").json() == {"n": 1}
    t.replay = True
    assert client.post("http://x/v1/a", content=b"same").json() == {"n": 1}      # 기록한 응답
    assert client.post("http://x/v1/a", content=b"other").json() == {"n": 2}     # 처음 보는 요청은 실제로
    assert len(calls) == 2 and t.misses == 1 and len(t.sent) == 3


def test_raw_values_are_fake_names_and_regex_matches():
    values = rg.raw_values("김민수 고객(010-1234-5678)에게 900101-1234567 확인")
    assert "김민수" in values and "010-1234-5678" in values and "900101-1234567" in values


def test_strict_expectation_blocks_confidential_rows_only():
    assert rg.expected_strict({"is_confidential": "1", "expected_route": "premium"}) == "block"
    assert rg.expected_strict({"is_confidential": "0", "expected_route": "cheap"}) == "cheap"
