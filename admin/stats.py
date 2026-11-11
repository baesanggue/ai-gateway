"""요청 기록 몇 건으로 현황 숫자를 만든다 (#90). 체험판에서는 테스터가 보낸 요청만으로 현황을 보여 준다.

RequestLog.totals()·breakdown()과 같은 모양을 돌려준다. DB 전체를 세는 대신 이미 가져온 줄로 센다.
"""
from datetime import date, datetime
from typing import Any


def summarize(rows: list[dict[str, Any]], today: date | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """(totals, breakdown). 값은 없다: 경로·사유·개인정보 종류별 개수와 비용만."""
    today = today or datetime.now().astimezone().date()
    by_route: dict[str, int] = {}
    reasons: dict[str, int] = {}
    pii: dict[str, int] = {}
    for r in rows:
        by_route[r["route"]] = by_route.get(r["route"], 0) + 1
        if r["route"] == "block":
            for reason in r["reasons"]:
                reasons[reason] = reasons.get(reason, 0) + 1
        for kind, n in r["pii_counts"].items():
            pii[kind] = pii.get(kind, 0) + n
    cost = sum(r["cost_usd"] for r in rows)
    base = sum(r["baseline_cost_usd"] for r in rows)
    totals = {"requests": len(rows), "cost_usd": cost, "baseline_cost_usd": base, "saved_usd": base - cost,
              "by_route": by_route}
    n_today = sum(datetime.fromisoformat(r["created_at"]).astimezone().date() == today for r in rows)
    return totals, {"block_reasons": reasons, "pii": pii, "today": n_today}
