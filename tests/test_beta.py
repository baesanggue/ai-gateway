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
