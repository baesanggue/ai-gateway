"""파인튜닝한 Laya 비교 (#22, D15): "판단까지 전부 로컬" 구성 B를 지금 구성 A(로컬 gliclass + 클라우드 Decisions)와
같은 입력·같은 지표로 잰다.

- 입력: #46과 같다. 테스트셋 190행을 조건 A(시연 사전 + 테스트셋 이름 전부)로 가명화한 문장 (run_masked.masker_for)
- B는 판단 5개(기밀·원본 필요·위험·난이도·의도)와 참고용 "남은 개인정보"를 laya-serve(/v1/systemone)의 모델 하나에 묻는다.
  원본 필요는 제품처럼 가명화된 개인정보가 있을 때만 묻는다
- 기준값: B는 학습 중 평가용 시범 데이터(eval-pilot.jsonl, 테스트셋과 무관)에서 고르고 테스트셋에 그대로 쓴다.
  0.5(보정된 확률 그대로)도 함께 적는다. A는 config.yaml 값 그대로다(M0에서 테스트셋으로 정한 값이라 A에 조금 유리하다)
- A는 다시 부르지 않고 #46 결과(bench/results/masked-2026-11-06/)를 읽는다. 입력이 같다
- 테스트셋은 학습에 쓰지 않았다 (D15)

실행 (laya-serve를 먼저 띄운다, docs/laya-serve-notes.md):
  uv run python bench/run_finetuned.py run --model laya-ko --out bench/results/finetune-2026-11-07
  uv run python bench/run_finetuned.py summary --out bench/results/finetune-2026-11-07
"""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_masked as rm  # noqa: E402
from spike_laya import truths  # noqa: E402

from gateway.config import load_settings  # noqa: E402
from gateway.decision.engines import DecisionError, SystemOneEngine  # noqa: E402
from gateway.decision.questions import INTENT_GROUP, QUESTIONS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "finetune" / "data" / "train-2026-11-07" / "eval-pilot.jsonl"
MASKED_46 = ROOT / "bench" / "results" / "masked-2026-11-06"
BINARY = {"is_confidential": 0.90, "needs_raw_value": 0.85, "is_risky": 0.95}   # D14 재현율 목표 (기준값을 고를 때)
A_THRESHOLDS = {"is_confidential": 0.10}                                       # A의 로컬 기밀 (config.yaml)


def ask(engine: SystemOneEngine, text: str, qids: list[str]) -> tuple[dict, float, str]:
    """질문마다 확률표. (확률, 지연 ms, 오류)"""
    try:
        r = engine.decide(text, {q: QUESTIONS[q] for q in qids})
    except DecisionError as e:
        return {}, 0.0, str(e)
    return {q: a.probs for q, a in r.answers.items()}, r.latency_ms, ""


def _yes(probs: dict, q: str):
    p = probs.get(q)
    return round(p["yes"], 4) if p else ""


def run(model: str, base_url: str, out: Path) -> None:
    settings = load_settings()
    engine = SystemOneEngine(base_url, model, 60)
    ask(engine, "예열", ["is_risky"])
    tag = model.replace(":", "_")
    # ① 시범 데이터 (기준값 고르기용): 질문 여섯 개를 모두 묻고, 정답이 있는 질문만 나중에 쓴다
    pilot = []
    for i, line in enumerate(EVAL.read_text(encoding="utf-8").splitlines()):
        row = json.loads(line)
        probs, latency, error = ask(engine, row["state"], list(QUESTIONS))
        pilot.append({"no": i, "expected": json.dumps(row["expected"], ensure_ascii=False),
                      "probs": json.dumps(probs, ensure_ascii=False), "latency_ms": round(latency, 1),
                      "error": error, "state": row["state"]})
    rm._write(out / f"pilot_{tag}.csv", pilot)
    print(f"{model}: 시범 {len(pilot)}줄 끝")
    # ② 테스트셋 (#46과 같은 가명화 문장)
    masker = rm.masker_for("A", settings)
    records = []
    for row in rm.load_rows():
        m = masker.mask([{"role": "user", "content": row["text"]}])
        masked = m.messages[0]["content"]
        qids = [q for q in QUESTIONS if q != "needs_raw_value" or m.has_pii]
        probs, latency, error = ask(engine, masked, qids)
        t = truths(row)
        records.append({
            "id": row["id"], "expected_route": row["expected_route"], "is_confidential_label": row["is_confidential"],
            "needs_raw_label": row["needs_raw_value"], "is_risky_label": row["is_risky"],
            "difficulty_truth": t.get("difficulty") or "", "intent_truth": t.get("intent") or "",
            "asked_needs_raw": int(m.has_pii), "p_has_pii": _yes(probs, "has_pii"),
            "p_confidential": _yes(probs, "is_confidential"), "p_needs_raw": _yes(probs, "needs_raw_value"),
            "p_risky": _yes(probs, "is_risky"),
            "p_complex": round(probs["difficulty"]["complex"], 4) if probs.get("difficulty") else "",
            "intent_probs": json.dumps(probs.get("intent") or {}, ensure_ascii=False),
            "latency_ms": round(latency, 1), "error": error, "input": masked})
    rm._write(out / f"testset_{tag}.csv", records)
    print(f"{model}: 테스트셋 {len(records)}행 끝")
    engine.close()


# ---------- 집계 ----------
def choose_thresholds(pilot: list[dict]) -> dict[str, float]:
    """시범 데이터에서: 예/아니오 판단은 D14 재현율 목표를 지키는 가장 높은 기준값, 난이도는 정확도가 가장 높은 기준값."""
    th = {}
    for q, target in BINARY.items():
        pos = [r["probs"][q]["yes"] for r in pilot if r["expected"].get(q) is True and r["probs"].get(q)]
        th[q] = next((t for t in sorted(set(pos), reverse=True) if sum(p >= t for p in pos) / len(pos) >= target),
                     0.5) if pos else 0.5
    diff = [(r["probs"]["difficulty"]["complex"], r["expected"]["difficulty"] == 1)
            for r in pilot if "difficulty" in r["expected"] and r["probs"].get("difficulty")]
    th["difficulty_complex"] = max(sorted({p for p, _ in diff}) or [0.5],
                                   key=lambda t: sum((p >= t) == c for p, c in diff)) if diff else 0.5
    return th


def metrics(rows: list[dict], th: dict[str, float]) -> dict[str, str]:
    """테스트셋에서 D14 지표. 원본 필요를 묻지 않은 행(가명화된 개인정보 없음)은 '아니오'."""
    rs = [r for r in rows if not r["error"]]

    def yes(r, key, q):
        return r[key] != "" and float(r[key]) >= th[q]

    def binary(label, key, q):
        pos = [r for r in rs if r[label] == "1"]
        neg = [r for r in rs if r[label] == "0"]
        return (f"{rm.rate(sum(yes(r, key, q) for r in pos), len(pos))}, 오탐 {rm.rate(sum(yes(r, key, q) for r in neg), len(neg))}",
                rm.auc([float(r[key]) for r in pos if r[key] != ""], [float(r[key]) for r in neg if r[key] != ""]))

    diff = [r for r in rs if r["difficulty_truth"] and r["p_complex"] != ""]
    diff_hit = sum(("complex" if float(r["p_complex"]) >= th["difficulty_complex"] else "simple") == r["difficulty_truth"]
                   for r in diff)
    intent = [r for r in rs if r["intent_truth"] and r["intent_probs"] not in ("", "{}")]
    intent_hit = 0
    for r in intent:
        probs = json.loads(r["intent_probs"])
        intent_hit += INTENT_GROUP[max(probs, key=probs.get)] == r["intent_truth"]
    out = {}
    out["is_confidential"], out["auc_confidential"] = binary("is_confidential_label", "p_confidential", "is_confidential")
    out["needs_raw_value"], out["auc_needs_raw"] = binary("needs_raw_label", "p_needs_raw", "needs_raw_value")
    out["is_risky"], out["auc_risky"] = binary("is_risky_label", "p_risky", "is_risky")
    out["difficulty"] = rm.rate(diff_hit, len(diff))
    out["intent"] = rm.rate(intent_hit, len(intent))
    out["errors"] = str(len(rows) - len(rs))
    return out


def a_metrics() -> dict[str, str]:
    """A(지금 구성)의 같은 지표를 #46 결과에서. 클라우드 판단은 cloud.csv, 로컬 기밀은 local.csv 조건 A·토큰 그대로."""
    with open(MASKED_46 / "cloud.csv", encoding="utf-8-sig", newline="") as f:
        cloud = [r for r in csv.DictReader(f) if r["demo"] == "0" and not r["error"]]
    with open(MASKED_46 / "local.csv", encoding="utf-8-sig", newline="") as f:
        local = [r for r in csv.DictReader(f) if r["demo"] == "0" and r["condition"] == "A" and r["variant"] == "token"]
    yes = ("True", "refused")

    def binary(label, value):
        pos = [r for r in cloud if r[label] == "1"]
        neg = [r for r in cloud if r[label] == "0"]
        return (f"{rm.rate(sum(r[value] in yes for r in pos), len(pos))}, "
                f"오탐 {rm.rate(sum(r[value] in yes for r in neg), len(neg))}")
    cpos = [float(r["p_confidential"]) for r in local if r["is_confidential"] == "1" and r["p_confidential"]]
    cneg = [float(r["p_confidential"]) for r in local if r["is_confidential"] == "0" and r["p_confidential"]]
    th = A_THRESHOLDS["is_confidential"]
    diff = [r for r in cloud if r["difficulty_truth"]]
    diff_hit = sum((r["difficulty"] if r["difficulty"] != "refused" else "complex") == r["difficulty_truth"] for r in diff)
    intent = [r for r in cloud if r["intent_truth"] and r["intent"] != "refused"]
    return {"is_confidential": f"{rm.rate(sum(p >= th for p in cpos), len(cpos))}, 오탐 {rm.rate(sum(p >= th for p in cneg), len(cneg))}",
            "auc_confidential": rm.auc(cpos, cneg),
            "needs_raw_value": binary("needs_raw_label", "needs_raw"), "is_risky": binary("is_risky_label", "risky"),
            "difficulty": rm.rate(diff_hit, len(diff)),
            "intent": rm.rate(sum(r["intent"] == r["intent_truth"] for r in intent), len(intent)),
            "latency_cloud_ms": f"{sum(float(r['latency_ms']) for r in cloud) / len(cloud):.0f}",
            "latency_local_ms": f"{sum(float(r['latency_ms']) for r in local) / len(local):.0f}"}


def _read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def summary(out: Path) -> None:
    a = a_metrics()
    lines = ["# 파인튜닝한 Laya 비교 (#22) — 판단까지 전부 로컬(B) vs 지금 구성(A)", "",
             "입력: 테스트셋 190행을 #46과 같이 가명화(조건 A). 테스트셋은 학습에 쓰지 않았다. "
             "B의 기준값은 시범 데이터(eval-pilot, 테스트셋과 무관)에서 골랐다. A는 #46 결과 그대로.", ""]
    models = sorted(p.stem.removeprefix("testset_") for p in out.glob("testset_*.csv"))
    cols = ["A: gliclass + Decisions"]
    table = {k: [a.get(k, "—")] for k in ("is_confidential", "auc_confidential", "needs_raw_value", "is_risky",
                                           "difficulty", "intent")}
    th_lines, lat = [], []
    for tag in models:
        rows = _read(out / f"testset_{tag}.csv")
        pilot = [{"expected": json.loads(r["expected"]), "probs": json.loads(r["probs"])}
                 for r in _read(out / f"pilot_{tag}.csv") if not r["error"]]
        th = choose_thresholds(pilot)
        for name, used in ((f"B: {tag} (시범에서 고른 기준값)", th), (f"B: {tag} (0.5)", dict.fromkeys(th, 0.5))):
            m = metrics(rows, used)
            cols.append(name)
            for k in table:
                table[k].append(m.get(k, "—"))
        th_lines.append(f"- `{tag}` 기준값: " + ", ".join(f"{q} {v:.4f}" for q, v in th.items()))
        ps = [float(r[k]) for r in rows for k in ("p_confidential", "p_needs_raw", "p_risky", "p_complex") if r[k] != ""]
        extreme = sum(p < 0.01 or p > 0.99 for p in ps)
        th_lines.append(f"  - 테스트셋 확률 중 0.01 미만·0.99 초과: {rm.rate(extreme, len(ps))} "
                        "(높으면 과신. 시범 데이터에서 고른 기준값이 극단값이 되기 쉬워 0.5 열을 대표값으로 본다)")
        missed = [f"{r['id']} {label}: {r['input']}" for r in rows if not r["error"]
                  for label, lab, key in (("위험", "is_risky_label", "p_risky"), ("기밀", "is_confidential_label", "p_confidential"))
                  if r[lab] == "1" and float(r[key]) < 0.5]
        th_lines += [f"  - 0.5에서 놓친 위험·기밀 ({len(missed)}개):"] + [f"    - {m}" for m in missed]
        ms = sorted(float(r["latency_ms"]) for r in rows if not r["error"])
        lat.append(f"- `{tag}`: 요청당 평균 {sum(ms) / len(ms):.0f}ms, p95 {ms[int(len(ms) * 0.95)]:.0f}ms "
                   f"(질문 5~6개 한 번에, CPU), 클라우드 호출 0")
    names = {"is_confidential": "기밀 (목표 재현율 ≥ 90%)", "auc_confidential": "기밀 AUC",
             "needs_raw_value": "원본 필요 (≥ 85%, 오탐 ≤ 2%)", "is_risky": "위험·인젝션 (≥ 95%, 오탐 ≤ 1%)",
             "difficulty": "난이도 (정확도 ≥ 85%)", "intent": "의도 (정확도 ≥ 90%)"}
    lines += ["| 판단 | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    lines += [f"| {names[k]} | " + " | ".join(v) + " |" for k, v in table.items()]
    lines += ["", "## 기준값", "", *th_lines, "", "## 지연", "",
              f"- A: 로컬 gliclass 평균 {a['latency_local_ms']}ms + 클라우드 Decisions 평균 {a['latency_cloud_ms']}ms (왕복), 요청당 클라우드 호출 1번 이상",
              *lat, ""]
    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["run", "summary"])
    ap.add_argument("--model", default="laya-ko")
    ap.add_argument("--base-url", default="http://127.0.0.1:8100")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.step == "run":
        run(args.model, args.base_url, args.out)
    else:
        summary(args.out)


if __name__ == "__main__":
    main()
