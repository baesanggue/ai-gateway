"""신뢰도 보정 (#60): 판단별 신뢰도 구간마다 실제 정확도, 그리고 `routing.min_confidence` 후보마다 경로가 어떻게 바뀌는지.

- 입력: #59 게이트웨이 벤치마크의 판단 기록(`requests.csv`, 기본 수준). 다시 부르지 않는다
- 신뢰도: 게이트웨이가 로그에 남긴 값. 예/아니오는 |2p − 1|, 난이도·의도는 (K·p_max − 1)/(K − 1) (service.confidence)
- 정답: 집계할 때의 testset 라벨 (난이도·의도는 경로에서 계산, spike_laya.truths)
- min_confidence는 난이도·의도에만 쓰인다(routing.policy): 기준 미만이면 low_confidence_route(고성능),
  무관 판단은 기준 미만이면 막지 않는다. 기록된 클라우드 판단을 decide_after_cloud에 다시 넣어 경로를 계산한다.
  클라우드 판단 전에 정해진 경로(템플릿·로컬 차단)는 그대로 둔다. 기준이 없을 때(null) 실제 경로와 같은지 먼저 확인한다
- 주의: 이 표는 테스트셋으로 계산한다. 여기서 고른 값은 테스트셋에 맞춘 값이라 M0 기준값과 같은 한계가 있다

실행: uv run python bench/run_calibration.py --gateway bench/results/gateway-2026-11-08 --out bench/results/calibration-2026-11-08
"""
import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import run_masked as rm  # noqa: E402
from spike_laya import truths  # noqa: E402

from gateway.config import load_settings  # noqa: E402
from gateway.decision.service import JudgeResult, Judgment  # noqa: E402
from gateway.routing.policy import decide_after_cloud  # noqa: E402

QUESTIONS = {"is_confidential": "local", "needs_raw_value": "cloud", "is_risky": "cloud",
             "difficulty": "cloud", "intent": "cloud"}
BINS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 0.9), (0.9, 1.0001)]
CANDIDATES = [None, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]


def load(gateway_dir: Path) -> list[dict]:
    with (gateway_dir / "requests.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["level"] == "basic"]
    labels = {r["id"]: r for r in rm.load_rows()}
    for r in rows:
        r["label"] = labels[r["id"]]
        r["truth"] = truths(labels[r["id"]])
        r["j"] = json.loads(r["judgments"])
    return rows


def judgments(rows: list[dict], q: str) -> list[tuple[float, bool]]:
    """(신뢰도, 맞았나). 판단 거부·정답 없음은 뺀다."""
    out = []
    for r in rows:
        j = r["j"].get(QUESTIONS[q], {}).get("judgments", {}).get(q)
        truth = r["truth"].get(q)
        if j is None or j["refused"] or truth is None or j["confidence"] is None:
            continue
        out.append((j["confidence"], j["value"] == truth))
    return out


def calibration_lines(rows: list[dict]) -> list[str]:
    head = " | ".join(f"{lo:.1f}–{min(hi, 1):.1f}" for lo, hi in BINS)
    lines = ["## 신뢰도 구간별 실제 정확도", "", "칸마다 `정확도 (맞음/개수)`. 신뢰도가 높을수록 정확도가 높아야 보정이 잘 된 것이다.", "",
             f"| 판단 (엔진) | {head} | 전체 |", "|---|" + "---|" * (len(BINS) + 1)]
    for q, engine in QUESTIONS.items():
        js = judgments(rows, q)
        cells = []
        for lo, hi in BINS:
            b = [ok for c, ok in js if lo <= c < hi]
            cells.append(rm.rate(sum(b), len(b)))
        lines.append(f"| {q} ({engine}) | " + " | ".join(cells) + f" | {rm.rate(sum(ok for _, ok in js), len(js))} |")
    return lines + [""]


def cloud_result(r: dict) -> JudgeResult | None:
    c = r["j"].get("cloud")
    if not c or c["error"]:
        return None
    js = {q: Judgment(q, v["probs"], v["value"], v["confidence"]) for q, v in c["judgments"].items()}
    return JudgeResult("cloud", c["model"], js)


def simulate(rows: list[dict], min_confidence: float | None, cfg) -> list[str]:
    cfg.routing.min_confidence = min_confidence
    out = []
    for r in rows:
        cloud = cloud_result(r)          # 없으면 클라우드 판단 전에 경로가 정해졌다 (템플릿, 가리지 못한 표기 차단)
        out.append(r["route"] if cloud is None else decide_after_cloud(cloud, cfg).route)
    return out


def simulation_lines(rows: list[dict]) -> list[str]:
    cfg = load_settings().config
    base = simulate(rows, None, cfg)
    same = sum(a == r["route"] for a, r in zip(base, rows))
    premium_cost = [float(r["cost_usd"]) for r in rows if r["route"] == "premium"]
    avg_premium = sum(premium_cost) / len(premium_cost) if premium_cost else 0.0
    lines = ["## min_confidence 후보마다 경로 (기록된 클라우드 판단으로 다시 계산)", "",
             f"확인: 기준 없음(null)으로 다시 계산한 경로가 실제 경로와 같은 행 {rm.rate(same, len(rows))}", "",
             "| min_confidence | 경로 정확도 | 고성능 | 무관 차단 | 바뀐 행 | 추가 비용 추정 (고성능 평균 × 저렴→고성능 행) |",
             "|---|---|---|---|---|---|"]
    for t in CANDIDATES:
        routes = simulate(rows, t, cfg)
        hit = sum(a == r["label"]["expected_route"] for a, r in zip(routes, rows))
        changed = [r["id"] for a, b, r in zip(routes, base, rows) if a != b]
        up = sum(1 for a, b in zip(routes, base) if b == "cheap" and a == "premium")
        off_topic = sum(1 for a, b, r in zip(routes, base, rows) if a == "block" and "off_topic" in r["reasons"])
        shown = ", ".join(changed[:8]) + (" …" if len(changed) > 8 else "")
        lines.append(f"| {t if t is not None else 'null (지금)'} | {rm.rate(hit, len(rows))} | {routes.count('premium')} | "
                     f"{off_topic} | {len(changed)}{': ' + shown if changed else ''} | +${up * avg_premium:.3f} |")
    return lines + [""]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    rows = load(args.gateway)
    lines = ["# 신뢰도 보정과 min_confidence (#60)", "",
             f"입력: `{args.gateway.as_posix()}` 기본 수준 {len(rows)}행의 판단 기록 (다시 부르지 않음). "
             "정답은 집계할 때의 testset 라벨. **이 표는 테스트셋으로 계산했다** — 여기서 고른 값은 테스트셋에 맞춘 값이다.", "",
             *calibration_lines(rows), *simulation_lines(rows)]
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
