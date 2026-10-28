"""run_judges.py 결과 분석 (#25).

예/아니오 판단은 질문별 기준값을 2겹 교차검증으로 정한다:
홀수 행으로 정한 기준값을 짝수 행에 적용하고, 짝수 행으로 정한 기준값을 홀수 행에 적용한 뒤 합쳐서 평가한다.
같은 문장으로 기준값을 정하고 평가하면 점수가 부풀려지기 때문이다.

실행: uv run python bench/analyze_judges.py <결과 폴더>   →  <폴더>/README.md
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_judges import TESTSET, mask_for_routing  # noqa: E402

DOMAINS = {"T": "쇼핑몰·사내", "M": "의료", "F": "금융", "G": "공공민원"}
LABELS = {"has_pii": "민감정보", "needs_raw_value": "원본 필요", "is_confidential": "기밀",
          "is_risky": "위험·인젝션", "difficulty": "난이도", "intent": "의도"}


def choose_threshold(pos: list[float], neg: list[float]) -> float:
    """'예'로 볼 기준값을 고른다 (p >= 기준값이면 '예').

    기준: 재현율 - 오탐률(Youden J)이 가장 큰 값. 같으면 더 낮은 기준값을 고른다.
    보안 판단에서는 놓치는 것(민감정보를 못 잡음)이 오탐보다 비싸기 때문이다.
    """
    if not pos or not neg:
        return 0.5
    best_t, best_j = 0.5, -1.0
    for t in sorted(set(pos) | set(neg)):
        j = sum(p >= t for p in pos) / len(pos) - sum(n >= t for n in neg) / len(neg)
        if j > best_j:  # 오름차순으로 보므로 같은 J면 먼저 나온(낮은) 기준값이 남는다
            best_t, best_j = t, j
    return best_t


def auc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    return sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))


def fold(rid: str) -> int:
    return int(rid[1:]) % 2


def binary_metrics(rows: list[dict], positive: str) -> dict:
    """rows: label, p. positive: 정답 '예'에 해당하는 label 값."""
    # 판단 거부(p 없음)는 확률이 없으므로 여기서는 빼고, 거부 건수는 따로 보고한다
    data = [(r["id"], float(r["p"]), r["label"] == positive) for r in rows
            if r["label"] not in ("", "None") and r["p"] != ""]
    pos = [p for _, p, y in data if y]
    neg = [p for _, p, y in data if not y]

    def rates(pred_yes: list[tuple[bool, bool]]):
        tp = sum(y and yhat for yhat, y in pred_yes)
        fp = sum((not y) and yhat for yhat, y in pred_yes)
        n_pos = sum(y for _, y in pred_yes)
        n_neg = len(pred_yes) - n_pos
        rec = tp / n_pos if n_pos else None
        fpr = fp / n_neg if n_neg else None
        acc = sum(yhat == y for yhat, y in pred_yes) / len(pred_yes)
        bal = (rec + (1 - fpr)) / 2 if rec is not None and fpr is not None else None
        return {"tp": tp, "n_pos": n_pos, "fp": fp, "n_neg": n_neg, "recall": rec, "fpr": fpr, "acc": acc, "bal": bal}

    default = rates([(p >= 0.5, y) for _, p, y in data])
    cv, cv_rows, thresholds = [], [], {}
    for f in (0, 1):
        fit = [(p, y) for rid, p, y in data if fold(rid) != f]
        t = choose_threshold([p for p, y in fit if y], [p for p, y in fit if not y])
        thresholds[f] = t
        cv += [(p >= t, y) for rid, p, y in data if fold(rid) == f]
        cv_rows += [(rid, p >= t, y) for rid, p, y in data if fold(rid) == f]
    full_t = choose_threshold(pos, neg)
    return {"default": default, "cv": rates(cv), "cv_rows": cv_rows, "auc": auc(pos, neg),
            "thr_folds": thresholds, "thr_all": full_t, "majority": max(len(pos), len(neg)) / len(data)}


def domain_section(sec, rout, local_model="gliclass:v4-edge") -> list[str]:
    """채택 구성(로컬: gliclass noul, 클라우드: Decisions 'cloud' 묶음)의 업종별 결과."""
    loc = next((r for m, r in sec if m["model"] == local_model), None)
    cld = next((r for m, r in rout if m["questions"] == "cloud"), None)
    if loc is None or cld is None:
        return []
    texts = {r["id"]: r["text"] for r in csv.DictReader(TESTSET.open(encoding="utf-8-sig", newline=""))}
    masked = {rid: mask_for_routing(t) != t for rid, t in texts.items()}

    def cv_rows(rows, key, variant, positive="True", refusal_yes=False, gated=False):
        rs = [r for r in rows if r["question"] == key and r["variant"] == variant]
        rs = [dict(r, p="1" if (refusal_yes and r["pred"] == "refusal") else r["p"]) for r in rs]
        if gated:
            rs = [dict(r, p=r["p"] if masked[r["id"]] else "0") for r in rs]
        return binary_metrics(rs, positive)["cv_rows"]

    items = [("민감정보 (로컬)", cv_rows(loc, "has_pii", "noul")),
             ("기밀 (로컬)", cv_rows(loc, "is_confidential", "noul")),
             ("원본 필요 (클라우드+조건)", cv_rows(cld, "needs_raw_value", "noul", refusal_yes=True, gated=True)),
             ("위험·인젝션 (클라우드)", cv_rows(cld, "is_risky", "noul", refusal_yes=True))]
    dif = cv_rows(cld, "difficulty", "score", positive="complex")
    intent = [(r["id"], r["pred"] == r["label"]) for r in cld
              if r["question"] == "intent" and r["label"] not in ("", "None")]

    out = ["", "## 업종별 결과 (채택 구성, 교차검증 기준값)", "",
           "| 업종 | " + " | ".join(f"{name}: 재현율 · 오탐" for name, _ in items) + " | 난이도 정확도 | 의도 정확도 |",
           "|---|" + "---|" * (len(items) + 2)]
    for prefix, dname in DOMAINS.items():
        cells = []
        for _, rows in items:
            rs = [(yhat, y) for rid, yhat, y in rows if rid.startswith(prefix)]
            tp = sum(y and yh for yh, y in rs)
            npos = sum(y for _, y in rs)
            fp = sum((not y) and yh for yh, y in rs)
            cells.append(f"{tp}/{npos} · {fp}/{len(rs) - npos}")
        d = [yh == y for rid, yh, y in dif if rid.startswith(prefix)]
        it = [ok for rid, ok in intent if rid.startswith(prefix)]
        cells += [f"{sum(d)}/{len(d)}", f"{sum(it)}/{len(it)}"]
        out.append(f"| {dname} ({prefix}) | " + " | ".join(cells) + " |")
    return out


def pct(x) -> str:
    return "—" if x is None else f"{x:.2f}"


def frac(a, b) -> str:
    return f"{a}/{b}"


def main():
    folder = Path(sys.argv[1])
    runs = []
    for meta_path in sorted(folder.glob("*.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        with meta_path.with_suffix(".csv").open(encoding="utf-8-sig", newline="") as f:
            runs.append((meta, list(csv.DictReader(f))))

    sec = [(m, r) for m, r in runs if m["questions"] == "security"]
    rout = [(m, r) for m, r in runs if m["questions"] in ("routing", "cloud")]
    any_meta = runs[0][0]
    lines = [
        f"# 100문장 비교: 로컬 보안 판단 + 클라우드 라우팅 ({folder.name})", "",
        f"- 실행 PC: {any_meta['pc']}",
        "- 테스트셋: `data/testset.csv` 100문장 (**라벨 검토 전**, #6 이후 재측정)",
        "- 기준값: 2겹 교차검증 (홀수 행으로 정해 짝수 행에 적용, 그 반대도 → 합쳐서 평가). "
        "고르는 규칙은 `analyze_judges.py`의 `choose_threshold` (재현율 − 오탐률 최대, 같으면 낮은 쪽)",
        "- 라우팅 판단은 가명화한 문장으로 측정 (`run_judges.py`의 `mask_for_routing`)", "",
    ]

    if sec:
        lines += ["## 보안 판단 (로컬)", "",
                  "| 판단 | 모델 | 형식 | AUC | 기준값 0.5: 재현율 · 오탐 | **보정 후: 재현율 · 오탐** | 보정 후 균형정확도 | 기준값 (전체로 정한 값) |",
                  "|---|---|---|---|---|---|---|---|"]
        for key in ("has_pii", "needs_raw_value", "is_confidential", "is_risky"):
            for meta, rows in sec:
                for variant in ("noul", "choice"):
                    rs = [r for r in rows if r["question"] == key and r["variant"] == variant]
                    if not rs:
                        continue
                    m = binary_metrics(rs, "True")
                    d, c = m["default"], m["cv"]
                    lines.append(
                        f"| {LABELS[key]} | `{meta['model']}` | {variant} | {pct(m['auc'])} | "
                        f"{frac(d['tp'], d['n_pos'])} · {frac(d['fp'], d['n_neg'])} | "
                        f"**{frac(c['tp'], c['n_pos'])} · {frac(c['fp'], c['n_neg'])}** | {pct(c['bal'])} | "
                        f"{m['thr_all']:.4f} |")
        # 원본 필요는 "가명화된 개인정보가 있을 때만" 의미가 있다 → 코드 조건으로 거른 경우
        texts = {r["id"]: r["text"] for r in csv.DictReader(TESTSET.open(encoding="utf-8-sig", newline=""))}
        has_masked = {rid: mask_for_routing(t) != t for rid, t in texts.items()}
        lines += ["", "### 원본 필요: 가명화된 개인정보가 있을 때만 묻기 (코드 조건)", "",
                  "개인정보가 없는 문장은 원본이 필요할 수 없으므로 판단 모델에 묻지 않고 '아니오'로 처리한 경우다.", "",
                  "| 모델 | 형식 | 보정 후: 재현율 · 오탐 | 보정 후 균형정확도 | (조건 없을 때 오탐) |", "|---|---|---|---|---|"]
        for meta, rows in sec:
            for variant in ("noul", "choice"):
                rs = [r for r in rows if r["question"] == "needs_raw_value" and r["variant"] == variant]
                plain = binary_metrics(rs, "True")["cv"]
                gated = [dict(r, p=r["p"] if has_masked[r["id"]] else "0") for r in rs]
                c = binary_metrics(gated, "True")["cv"]
                lines.append(f"| `{meta['model']}` | {variant} | **{frac(c['tp'], c['n_pos'])} · {frac(c['fp'], c['n_neg'])}** | "
                             f"{pct(c['bal'])} | {frac(plain['fp'], plain['n_neg'])} |")
        lines += ["", "| 모델 | 요청 1건 지연 (평균 / p95, 질문 8개) | 질문당 |", "|---|---|---|"]
        for meta, _ in sec:
            lines.append(f"| `{meta['model']}` | {meta['latency_ms_mean']} / {meta['latency_ms_p95']} ms "
                         f"({meta['latency_basis']}) | {meta['latency_ms_mean'] / meta['n_questions']:.0f} ms |")

    for meta, rows in rout:
        lines += ["", f"## 클라우드 판단: `{meta['model']}` (질문 묶음: {meta['questions']}, 질문 {meta['n_questions']}개)", ""]
        for key in ("is_risky", "needs_raw_value"):
            qrows = [r for r in rows if r["question"] == key]
            if not qrows:
                continue
            # 판단 거부는 게이트웨이에서 차단 쪽으로 처리하므로 P(예)=1로 둔다
            rk = [dict(r, p=r["p"] if r["pred"] != "refusal" else "1") for r in qrows]
            m = binary_metrics(rk, "True")
            d, c = m["default"], m["cv"]
            lines += [f"- {LABELS[key]} (predicate, 거부는 '예'로 처리): AUC {pct(m['auc'])} · "
                      f"기준값 0.5 재현율 {frac(d['tp'], d['n_pos'])}, 오탐 {frac(d['fp'], d['n_neg'])} · "
                      f"보정 후 재현율 {frac(c['tp'], c['n_pos'])}, 오탐 {frac(c['fp'], c['n_neg'])}"]
        if any(r["question"] in ("is_risky", "needs_raw_value") for r in rows):
            lines.append("")
        lines += ["| 판단 | 형식 | n | AUC | 기준값 0.5 정확도 | 보정 후 정확도 · 균형정확도 | 다수결 기준선 |",
                  "|---|---|---|---|---|---|---|"]
        for variant in ("score", "choice"):
            rs = [r for r in rows if r["question"] == "difficulty" and r["variant"] == variant]
            m = binary_metrics(rs, "complex")
            lines.append(f"| 난이도 (복잡=예) | {variant} | {m['default']['n_pos'] + m['default']['n_neg']} | "
                         f"{pct(m['auc'])} | {m['default']['acc']:.2f} | {m['cv']['acc']:.2f} · {pct(m['cv']['bal'])} | "
                         f"{m['majority']:.2f} |")
        it = [r for r in rows if r["question"] == "intent" and r["label"] not in ("", "None")]
        labels = [r["label"] for r in it]
        acc = sum(r["pred"] == r["label"] for r in it) / len(it)
        majority = max(Counter(labels).values()) / len(it)
        lines += ["", f"- 의도 (choice): 정확도 **{acc:.2f}** (n={len(it)}, 다수결 기준선 {majority:.2f})"]
        for cls in ("faq", "task", "off_topic"):
            rs = [r for r in it if r["label"] == cls]
            lines.append(f"  - {cls}: {sum(r['pred'] == cls for r in rs)}/{len(rs)} 맞힘")
        conf = Counter((r["label"], r["pred"]) for r in it if r["label"] != r["pred"])
        if conf:
            lines.append("  - 틀린 경우 (정답 → 예측): " + ", ".join(f"{a}→{b} {n}건" for (a, b), n in conf.most_common()))
        refused = sorted({r["id"] for r in rows if r["pred"] == "refusal"})
        lines += [f"- 판단 거부(refusal): {len(refused)}문장" + (f" — {', '.join(refused)} (난이도 집계에서 제외, 의도는 오답 처리)" if refused else ""),
                  f"- 지연: 평균 {meta['latency_ms_mean']} ms · p95 {meta['latency_ms_p95']} ms ({meta['latency_basis']})",
                  f"- 입력 토큰 합계: {meta['input_tokens_total']:,} (100요청)"]

    lines += domain_section(sec, rout)
    (folder / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
