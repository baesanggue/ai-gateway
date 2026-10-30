"""판단 거부 재질문 시험 (#8, D14).

게이트웨이 판단 서비스(gateway/decision/service.py)의 judge_cloud를 테스트셋 190행에 그대로 돌리고,
거부된 라우팅 질문(난이도·의도)만 따로 다시 물으면 풀리는지 잰다.
- 입력은 측정용 가명화 근사(run_judges.mask_for_routing). 원본 필요 질문은 가명화된 토큰이 있을 때만 묻는다
- D14 지표: 차단 대상이 아닌 요청 중 판단 거부 때문에 막히는 비율 (목표 ≤ 1%)
  - 정책 A "거부 → 차단": 다시 물어도 거부가 남으면 막힌다
  - 정책 B "라우팅 질문 거부 → 고성능, 위험·원본 필요 질문 거부 → 차단" (#8에서 채택)
- 결과(10/30): 26문장 모두 다시 물어도 똑같이 거부 → 게이트웨이에서는 재질문을 하지 않는다

실행: uv run python bench/retry_refusals.py --out-dir bench/results/refusal-retry-<날짜>
"""
import argparse
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_judges import mask_for_routing  # noqa: E402

from gateway.config import load_settings  # noqa: E402
from gateway.decision.engines import DecisionError  # noqa: E402
from gateway.decision.questions import QUESTIONS, ROUTING_QUESTIONS  # noqa: E402
from gateway.decision.service import DecisionService  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTSET = ROOT / "data" / "testset.csv"
TOKEN = re.compile(r"\[[^\]\d]+\d+\]")
SAFETY = ("is_risky", "needs_raw_value")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--ids", help="쉼표로 구분한 id만 (기본: 190행 전체)")
    args = ap.parse_args()

    with TESTSET.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if args.ids:
        wanted = set(args.ids.split(","))
        rows = [r for r in rows if r["id"] in wanted]

    svc = DecisionService.from_settings(load_settings())
    records = []
    try:
        for row in rows:
            masked = mask_for_routing(row["text"])
            r = svc.judge_cloud(masked, has_masked_pii=bool(TOKEN.search(masked)))
            j = r.judgments
            first = [q for q in ROUTING_QUESTIONS if q in j and j[q].refused]
            still, error = list(first), r.error or ""
            if first:  # 거부된 라우팅 질문만 따로 다시 묻는다
                try:
                    again = svc.cloud.decide(masked, {q: QUESTIONS[q] for q in first})
                    still = [q for q in first if again.answers[q].refused]
                except DecisionError as e:
                    error = str(e)
            safety_refused = [q for q in SAFETY if q in j and j[q].refused]
            blocked_by_judgment = any(q in j and (j[q].refused or j[q].value) for q in SAFETY)
            records.append({
                "id": row["id"], "expected_route": row["expected_route"], "input": masked, "error": error,
                "first_refused": ",".join(first), "still_refused": ",".join(still),
                "safety_refused": ",".join(safety_refused),
                "blocked_by_risky_or_raw": blocked_by_judgment,
                "is_risky_p": j["is_risky"].probs["yes"] if "is_risky" in j and j["is_risky"].probs else "",
                "needs_raw_p": (j["needs_raw_value"].probs["yes"]
                                if "needs_raw_value" in j and j["needs_raw_value"].probs else ""),
                "difficulty": j["difficulty"].value if "difficulty" in j else "",
                "intent": j["intent"].value if "intent" in j else "",
            })
            print(row["id"], ",".join(first) or "-", "→", ",".join(still) or "-", flush=True)
    finally:
        svc.close()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.out_dir / f"{date.today().isoformat()}-retry-refusals"
    with open(f"{stem}.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(records[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(records)

    normal = [r for r in records if r["expected_route"] != "block"]
    blocked_a = sorted(r["id"] for r in normal if r["still_refused"] or r["safety_refused"])
    blocked_b = sorted(r["id"] for r in normal if r["safety_refused"])
    meta = {
        "rows": len(records), "errors": sum(bool(r["error"]) for r in records),
        "first_refused_rows": sum(bool(r["first_refused"]) for r in records),
        "still_refused_rows": sum(bool(r["still_refused"]) for r in records),
        "safety_refused_rows": sum(bool(r["safety_refused"]) for r in records),
        # 거부가 남았지만 위험·원본 필요 판단으로 이미 차단되는 요청 (거부 규칙이 없어도 막힘)
        "still_refused_but_blocked_anyway": sorted(
            r["id"] for r in records if r["still_refused"] and r["blocked_by_risky_or_raw"]),
        "normal_rows": len(normal),
        "policy_a_normal_blocked": blocked_a,
        "policy_a_rate": round(len(blocked_a) / len(normal), 4),
        "policy_b_normal_blocked": blocked_b,
        "policy_b_rate": round(len(blocked_b) / len(normal), 4),
    }
    Path(f"{stem}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8", newline="\n")
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
