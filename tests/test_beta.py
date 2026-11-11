"""웹 체험판 (#90): 하루 사용량, 비밀번호 비교, 내 요청만으로 만든 현황 숫자, 비밀번호가 출력되지 않는지."""
from datetime import date, timedelta
from pathlib import Path

from admin.beta import Quota, password_ok
from admin.stats import summarize
from gateway.config import load_settings

ROOT = Path(__file__).resolve().parent.parent


def test_daily_quota_is_shared_and_resets_next_day():
    q = Quota(per_day=2)
    d = date(2026, 11, 12)
    assert [q.take(d), q.take(d), q.take(d)] == [True, True, False]       # 하루 전체 상한
    assert q.take(d + timedelta(days=1))                                   # 다음 날 다시
    assert q.used == 1


def test_password_compare():
    assert password_ok("garim-1234", "garim-1234")
    assert not password_ok("garim-123", "garim-1234")
    assert not password_ok("", "")                                         # 비밀번호가 비어 있으면 아무도 못 들어온다


def row(route, cost, base, reasons=(), pii=None, created="2026-11-12T03:00:00.000+00:00"):
    return {"route": route, "cost_usd": cost, "baseline_cost_usd": base, "reasons": list(reasons),
            "pii_counts": pii or {}, "created_at": created}


def test_summarize_counts_only_the_given_rows():
    rows = [row("template", 0.0, 0.001), row("cheap", 0.0001, 0.001, pii={"고객": 1, "전화번호": 1}),
            row("block", 0.0, 0.0, reasons=["needs_raw"], pii={"주민번호": 1}, created="2026-11-11T03:00:00.000+00:00")]
    totals, b = summarize(rows, today=date(2026, 11, 12))
    assert totals["requests"] == 3 and totals["by_route"] == {"template": 1, "cheap": 1, "block": 1}
    assert abs(totals["saved_usd"] - (0.002 - 0.0001)) < 1e-12
    assert b == {"block_reasons": {"needs_raw": 1}, "pii": {"고객": 1, "전화번호": 1, "주민번호": 1}, "today": 2}
    assert summarize([])[0]["requests"] == 0


def test_beta_password_is_read_but_never_printed(tmp_path):
    env = tmp_path / ".env"
    env.write_text("BETA_PASSWORD=beta-secret-777\n", encoding="utf-8")
    s = load_settings(ROOT / "config.yaml", env_file=env)
    assert s.secrets.beta_password.get_secret_value() == "beta-secret-777"
    assert s.config.beta.per_session == 30 and s.config.beta.per_day == 500
    for text in (repr(s), str(s), s.model_dump_json()):
        assert "beta-secret-777" not in text


def test_login_guard_counts_failures_across_sessions_per_client_and_globally():
    """세션마다 세면 새로고침으로 끝없이 시도할 수 있다 (11/11 보안 검토). 서버가 주소별·전체로 센다."""
    from admin.beta import LoginGuard
    g = LoginGuard(per_client=3, global_max=5, window=600, lock=600)
    for _ in range(3):
        g.fail("1.1.1.1", now=100)
    assert g.locked("1.1.1.1", now=101) and not g.locked("2.2.2.2", now=101)   # 그 주소만 막힘
    assert not g.locked("1.1.1.1", now=701)                                     # 시간이 지나면 풀림
    for ip in ("3.3.3.3", "4.4.4.4"):                                           # 전체 5번째 → 모두 막힘
        g.fail(ip, now=200)
    assert g.locked("9.9.9.9", now=201)


def test_login_guard_forgets_old_failures_and_success_resets_client():
    from admin.beta import LoginGuard
    g = LoginGuard(per_client=3, global_max=100, window=60, lock=600)
    g.fail("1.1.1.1", now=0)
    g.fail("1.1.1.1", now=10)
    g.fail("1.1.1.1", now=100)                    # 앞의 둘은 60초 창 밖
    assert not g.locked("1.1.1.1", now=101)
    g.fail("1.1.1.1", now=102)
    g.succeed("1.1.1.1")
    g.fail("1.1.1.1", now=103)
    assert not g.locked("1.1.1.1", now=104)


def test_tunnel_header_is_trusted_only_for_loopback_connections():
    """인터넷 요청은 이 PC의 터널(루프백)을 거친다. 직접 들어온 요청의 머리글은 믿지 않는다 (11/11 보안 검토)."""
    from admin.beta import client_key
    assert client_key("127.0.0.1", "203.0.113.9") == "cf:203.0.113.9"
    assert client_key("::1", "203.0.113.9") == "cf:203.0.113.9"
    assert client_key("198.51.100.4", "203.0.113.9") == "198.51.100.4"      # 꾸민 머리글
    assert client_key("127.0.0.1", None) == "127.0.0.1"
    # Streamlit은 루프백 연결의 주소를 None으로 준다. 터널로 온 요청이 이 경우다 (모두 한 키로 묶이면 한 사람이 모두를 잠근다)
    assert client_key(None, "203.0.113.9") == "cf:203.0.113.9"
    assert client_key(None, "not-an-ip") == "local" and client_key(None, None) == "local"
