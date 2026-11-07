"""사전에 없는 이름 찾기 측정 (#53): 코드 후보 추출 + 로컬 판단 "이게 사람 이름인가?"

세 세트 (모두 시연 사전만 쓴다 → 사전에 없는 이름이 남는다)
  testset  테스트셋 190행. **기준값은 여기서 고른다**
  pilot    파일럿 생성 문장 246개 (finetune/data/pilot-2026-10-31, 테스트셋과 겹친 것 제외).
           문장도 이름도 테스트셋과 다르다. 개인정보 낱말만 있는 문장(pii_word_only)이 잘못 가리기를 본다
  swap     테스트셋 이름을 드문 성씨·두 글자 성씨·외자 이름으로 바꾼 문장. 후보 규칙이 덮는 폭을 본다
기준값을 testset에서 고른 뒤 pilot·swap에는 그 값을 그대로 쓴다 (테스트셋에 맞춘 점수가 부풀지 않게).

질문 형식
  noul    "이 요청에서 '<후보>'는 사람 이름인가?" (gateway/masking/names.py와 같다)
  choice  "이 요청에서 '<후보>'는 무엇인가?" — 사람 이름 / 사람을 가리키는 일반 낱말 / 그 밖의 낱말. P(사람 이름)을 쓴다
          (noul에서 "고객"·"민원인"을 이름으로 봐서, 그것을 따로 고를 자리를 준다)

이름 인식 모델 (방법 2, #42): `ner` 단계. 판단 모델과 달리 원문(로컬)을 보고 NAME 위치를 찾는다.
  가명화 부품에 넣으면 사전·정규식보다 먼저 원문에서 찾게 되므로, 측정도 원문으로 한다.
  기준값은 NAME 조각 확률 평균에 적용한다.

실행:
  uv run python bench/run_names.py run --model gliclass:v4-edge --question noul --out-dir bench/results/names-2026-11-07
  uv run python bench/run_names.py ner --onnx onnx/model_int8.onnx --out-dir bench/results/names-2026-11-07
  uv run python bench/run_names.py summary --out-dir bench/results/names-2026-11-07     # 폴더의 결과 전부 비교
"""
import argparse
import csv
import importlib.util
import json
import re
import statistics
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_masked as rm  # noqa: E402  (가명화 조건 B, 테스트셋 이름 목록)

from gateway.config import load_settings  # noqa: E402
from gateway.decision.engines import DecisionError, SystemOneEngine  # noqa: E402
from gateway.decision.questions import Question  # noqa: E402
from gateway.masking.names import NAME_QUESTION, candidates  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PILOT = ROOT / "finetune" / "data" / "pilot-2026-10-31" / "items.csv"
# 테스트셋 이름 32개를 바꿀 가상 이름. 드문 성씨, 두 글자 성씨, 외자 이름, 후보 규칙의 성씨 목록에 없는 성씨(묵·섭)
RARE = ["남궁민", "황보혜인", "제갈윤", "선우진", "독고현", "엄태오", "변소라", "염지환", "추나래", "도해솔",
        "석주원", "설아린", "마동희", "연바다", "위서율", "명지성", "기하람", "왕수경", "금채린", "옥도경",
        "육지안", "인서하", "맹은솔", "모예린", "국찬우", "편세아", "용재희", "봉수민", "왕휘", "온결",
        "묵하진", "섭지우"]
TARGET_RECALL = 0.95
TARGET_WRONG = 0.05
CHOICE_QUESTION = "이 요청에서 '{}'는 무엇인가?"
CHOICE_CRITERIA = {"name": "사람의 이름 (성과 이름, 예: 홍길동)",
                   "person_word": "사람을 가리키는 일반 낱말 (고객, 직원, 민원인, 환자, 우리)",
                   "other": "그 밖의 낱말"}


def _generator():
    spec = importlib.util.spec_from_file_location("generate", ROOT / "finetune" / "generate.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    return gen


def build_sets() -> dict[str, list[dict]]:
    """세트별 문장과 정답 이름. 문장은 아직 가명화 전 원문."""
    rows = rm.load_rows()
    testset = [{"id": r["id"], "text": r["text"], "names": [n for n in rm.NAMES if n in r["text"]]} for r in rows]
    gen = _generator()
    faker_name = re.compile(f"[{gen.SURNAMES[:30]}](?:{'|'.join(gen.GIVEN)})")
    with PILOT.open(encoding="utf-8-sig", newline="") as f:
        pilot = [{"id": f"P{i:03d}-{r['category']}", "text": r["original"],
                  "names": list(dict.fromkeys(faker_name.findall(r["original"])))}
                 for i, r in enumerate(csv.DictReader(f)) if not r["drop"]]
    swap_map = dict(zip(rm.NAMES, RARE))
    swap = []
    for r in rows:
        present = [n for n in rm.NAMES if n in r["text"]]
        if present:
            text = r["text"]
            for n in present:
                text = text.replace(n, swap_map[n])
            swap.append({"id": f"S-{r['id']}", "text": text, "names": [swap_map[n] for n in present]})
    return {"testset": testset, "pilot": pilot, "swap": swap}


def ask(engine: SystemOneEngine, text: str, values: list[str], mode: str) -> tuple[dict[str, float], float, str]:
    """이름 모양마다 P(사람 이름). (확률, 지연 ms, 오류)"""
    if not values:
        return {}, 0.0, ""
    if mode == "noul":
        qs = {f"name{i}": Question("noul", NAME_QUESTION.format(v)) for i, v in enumerate(values)}
    else:
        qs = {f"name{i}": Question("choice", CHOICE_QUESTION.format(v), CHOICE_CRITERIA) for i, v in enumerate(values)}
    try:
        r = engine.decide(text, qs)
    except DecisionError as e:
        return {}, 0.0, str(e)
    key = "yes" if mode == "noul" else "name"
    return {v: round(r.answers[f"name{i}"].probs[key], 4) for i, v in enumerate(values)}, r.latency_ms, ""


def run_file(out: Path, model: str, mode: str) -> Path:
    return out / f"{model.replace(':', '_')}-{mode}.csv"


def run(out: Path, model: str | None, mode: str) -> None:
    settings = load_settings()
    d = settings.config.decision
    model = model or d.model
    engine = SystemOneEngine(d.base_url, model, max(d.timeout_seconds, 30))
    masker = rm.masker_for("B", settings)
    ask(engine, "예열 김예열", ["김예열"], mode)               # 첫 요청은 모델을 올리느라 느리다
    records = []
    try:
        for name, items in build_sets().items():
            for it in items:
                masked = masker.mask([{"role": "user", "content": it["text"]}]).messages[0]["content"]
                left = [n for n in it["names"] if n in masked]  # 사전·정규식이 이미 가린 이름은 정답에서 뺀다
                options = [c.options for c in candidates(masked)]
                probs, latency, error = ask(engine, masked, list(dict.fromkeys(v for o in options for v in o)), mode)
                records.append({"set": name, "id": it["id"], "names": "|".join(left),
                                "options": json.dumps(options, ensure_ascii=False),
                                "probs": json.dumps(probs, ensure_ascii=False),
                                "questions": len(probs), "latency_ms": round(latency, 1),
                                "error": error, "masked": masked})
            print(f"{model} {mode}: {name} 끝 ({len(items)}문장)")
    finally:
        engine.close()
    rm._write(run_file(out, model, mode), records)


def run_ner(out: Path, onnx_file: str) -> None:
    import time

    from gateway.masking.ner import NerModel
    settings = load_settings()
    masker = rm.masker_for("B", settings)
    ner = NerModel(ROOT / "models" / "koelectra-ko-pii-ner", onnx_file)
    ner.find("예열 김예열")
    records = []
    for name, items in build_sets().items():
        for it in items:
            masked = masker.mask([{"role": "user", "content": it["text"]}]).messages[0]["content"]
            left = [n for n in it["names"] if n in masked]
            t0 = time.perf_counter()
            spans = ner.find(it["text"])
            latency = (time.perf_counter() - t0) * 1000
            names = [s for s in spans if s.label == "NAME"]
            probs: dict[str, float] = {}
            for s in names:
                probs[s.text] = max(probs.get(s.text, 0.0), s.score)
            records.append({"set": name, "id": it["id"], "names": "|".join(left), "all_names": "|".join(it["names"]),
                            "options": json.dumps([[s.text] for s in names], ensure_ascii=False),
                            "probs": json.dumps(probs, ensure_ascii=False), "questions": 1,
                            "latency_ms": round(latency, 1), "error": "",
                            "other": json.dumps([[s.label, s.text] for s in spans if s.label != "NAME"],
                                                ensure_ascii=False),
                            "masked": it["text"]})
        print(f"ner {onnx_file}: {name} 끝 ({len(items)}문장)")
    rm._write(out / f"ner_koelectra-{Path(onnx_file).stem}.csv", records)


# ---------- 집계 ----------
def found_names(row: dict, th: float) -> list[str]:
    """낱말마다 기준값 이상인 가장 긴 이름 모양 (NameFinder와 같은 규칙)."""
    probs = json.loads(row["probs"])
    out = []
    for options in json.loads(row["options"]):
        hit = next((v for v in options if probs.get(v, 0) >= th), None)
        if hit:
            out.append(hit)
    return list(dict.fromkeys(out))


def covers(found: str, name: str) -> bool:
    """찾은 조각이 이름을 다 가리는가 ("양미래" 또는 "양미래를"처럼 뒤에 조사까지)."""
    return found == name or found.startswith(name)


def score(rows: list[dict], th: float) -> dict:
    names = hit = partial = wrong = 0
    wrong_ids = []
    for r in rows:
        truth = [n for n in r["names"].split("|") if n]
        # 사전이 이미 가린 이름(김민수 등)을 이름 인식 모델이 또 찾아도 잘못 가린 것이 아니다
        known = [n for n in r.get("all_names", r["names"]).split("|") if n] or truth
        found = found_names(r, th)
        names += len(truth)
        hit += sum(any(covers(f, n) for f in found) for n in truth)
        partial += sum(not any(covers(f, n) for f in found) and any(f != n and n.startswith(f) for f in found)
                       for n in truth)
        bad = [f for f in found if not any(covers(f, n) or n.startswith(f) for n in known)]   # 이름이 아닌 것을 이름으로
        if bad:
            wrong += 1
            wrong_ids.append((r["id"], bad))
    return {"names": names, "hit": hit, "partial": partial, "wrong": wrong, "sentences": len(rows),
            "recall": hit / names if names else None, "wrong_rate": wrong / len(rows) if rows else None,
            "wrong_ids": wrong_ids}


def choose_threshold(rows: list[dict]) -> float:
    """재현율 목표를 지키는 가장 높은 기준값 (잘못 가리기가 가장 적다)."""
    ths = sorted({p for r in rows for p in json.loads(r["probs"]).values()}, reverse=True)
    for th in ths:
        if (score(rows, th)["recall"] or 0) >= TARGET_RECALL:
            return th
    return 0.0


def _pct(n: int, d: int) -> str:
    return f"{n / d:.0%} ({n}/{d})" if d else "—"


SETS = ("testset", "pilot", "swap")


def evaluate(rows: list[dict], fixed: float | None = None) -> dict:
    """testset에서 기준값을 고르고 세 세트에 그대로 적용한다. fixed를 주면 고르지 않고 그 값을 쓴다."""
    by = {s: [r for r in rows if r["set"] == s] for s in SETS}
    th = fixed if fixed is not None else choose_threshold(by["testset"])
    scores = {s: score(rs, th) for s, rs in by.items()}
    ok = all((sc["recall"] is None or sc["recall"] >= TARGET_RECALL) and sc["wrong_rate"] <= TARGET_WRONG
             for sc in scores.values())
    lat = [float(r["latency_ms"]) for r in rows if int(r["questions"]) > 0]
    return {"by": by, "th": th, "scores": scores, "ok": ok, "lat": lat,
            "questions": [int(r["questions"]) for r in rows], "errors": sum(1 for r in rows if r["error"])}


def summary(out: Path) -> None:
    # 이름 인식 모델은 라벨을 직접 붙이므로 기준값 없이 모델 판단 그대로 쓴다 (테스트셋에 맞춘 값이 아니다)
    runs = {p.stem: evaluate(list(csv.DictReader(p.open(encoding="utf-8-sig"))),
                             0.0 if p.stem.startswith("ner_") else None) for p in sorted(out.glob("*-*.csv"))}
    lines = [f"# 사전에 없는 이름 찾기 (#53) — {date.today().isoformat()}", "",
             "방법: 코드가 이름 후보(성씨로 시작하는 2~4글자 낱말, 조사·호칭 뗌)를 뽑고, 로컬 판단 모델이 후보마다 사람 이름인지 판단한다. "
             "사전은 시연 사전만 쓴다 (사전에 없는 이름이 남는다).", "",
             f"목표: 사전에 없는 이름 재현율 ≥ {TARGET_RECALL:.0%}, 잘못 가린 문장 ≤ {TARGET_WRONG:.0%}. "
             f"판단 모델의 기준값은 testset에서 재현율 {TARGET_RECALL:.0%}를 지키는 가장 높은 값으로 고르고, pilot·swap에는 그대로 쓴다. "
             "이름 인식 모델(`ner_`)은 라벨을 직접 붙이므로 기준값 없이 모델 판단 그대로 쓴다 (0.000).", "",
             "| 모델-질문 | 기준값 | testset 재현율 | testset 잘못 가림 | pilot 재현율 | pilot 잘못 가림 | swap 재현율 | swap 잘못 가림 | 평균 지연 | 목표 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for name, ev in runs.items():
        cells = [f"{_pct(sc['hit'], sc['names'])} | {_pct(sc['wrong'], sc['sentences'])}" for sc in ev["scores"].values()]
        lines.append(f"| {name} | {ev['th']:.3f} | " + " | ".join(cells) +
                     f" | {statistics.mean(ev['lat']):.0f}ms | {'✅' if ev['ok'] else '❌'} |")
    best_name = min(runs, key=lambda k: (runs[k]["scores"]["pilot"]["wrong_rate"], -(runs[k]["scores"]["pilot"]["recall"] or 0)))
    best = runs[best_name]
    qs = best["questions"]
    lines += ["", f"## 가장 나은 것: `{best_name}` (pilot에서 잘못 가린 문장이 가장 적음)", "",
              f"지연 (후보가 있는 문장, 로컬 CPU): 평균 {statistics.mean(best['lat']):.0f}ms, "
              f"p95 {sorted(best['lat'])[int(len(best['lat']) * 0.95)]:.0f}ms / 문장당 질문 평균 {statistics.mean(qs):.1f}, "
              f"최대 {max(qs)} / 오류 {best['errors']}문장", "",
              "| 기준값 | testset 재현율 | testset 잘못 가림 | pilot 재현율 | pilot 잘못 가림 | swap 재현율 | swap 잘못 가림 |",
              "|---|---|---|---|---|---|---|"]
    for t in sorted({0.1, 0.2, 0.3, 0.5, 0.7, best["th"]}):
        cells = []
        for s in SETS:
            sc = score(best["by"][s], t)
            cells += [_pct(sc["hit"], sc["names"]), _pct(sc["wrong"], sc["sentences"])]
        lines.append(f"| {t:.3f}{' (고른 값)' if t == best['th'] else ''} | " + " | ".join(cells) + " |")
    lines += ["", "### 놓친 이름과 잘못 가린 낱말 (고른 기준값)", ""]
    for s in SETS:
        rs, sc = best["by"][s], best["scores"][s]
        missed = [(r["id"], n) for r in rs for n in r["names"].split("|") if n and n not in found_names(r, best["th"])]
        lines.append(f"- **{s}** 놓침: " + (", ".join(f"{i} {n}" for i, n in missed) or "없음"))
        lines.append(f"- **{s}** 잘못 가림: " + (", ".join(f"{i} {'/'.join(b)}" for i, b in sc["wrong_ids"]) or "없음"))
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"저장: {out / 'summary.md'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("step", choices=["run", "ner", "summary"])
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--model", help="Ollaya 모델 (기본: config.yaml)")
    ap.add_argument("--question", choices=["noul", "choice"], default="noul")
    ap.add_argument("--onnx", default="onnx/model_int8.onnx", help="이름 인식 모델 파일 (models/koelectra-ko-pii-ner 안)")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    if args.step == "run":
        run(args.out_dir, args.model, args.question)
    elif args.step == "ner":
        run_ner(args.out_dir, args.onnx)
    else:
        summary(args.out_dir)


if __name__ == "__main__":
    main()
