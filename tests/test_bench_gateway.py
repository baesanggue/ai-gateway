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


def test_cost_by_route_and_mix_assumption():
    """경로별 절감과, 복잡한 요청 비율에 따른 절감률(측정한 경로별 평균으로 계산). 차단은 절감액에 넣지 않는다 (D18)."""
    def row(route, cost, base, tokens=0):
        return {"route": route, "cost_usd": str(cost), "baseline_cost_usd": str(base), "completion_tokens": str(tokens)}
    basic = [row("template", 0, 0.002), row("cheap", 0.001, 0.02, 100), row("premium", 0.01, 0.01, 1000),
             row("block", 0, 0)]
    text = "\n".join(rg.cost_lines(basic))
    assert "| template | 1 | $0.0000 | $0.0020 | 100% |" in text
    assert "| premium | 1 | $0.0100 | $0.0100 | 0% |" in text
    # 복잡 10%: 게이트웨이 0.1×0.01 + 0.9×0.0005, 전부 고성능 0.1×0.01 + 0.9×0.011 → 절감 87%
    assert "| 10% | 87% |" in text


def test_strict_expectation_blocks_confidential_rows_only():
    assert rg.expected_strict({"is_confidential": "1", "expected_route": "premium"}) == "block"
    assert rg.expected_strict({"is_confidential": "0", "expected_route": "cheap"}) == "cheap"
