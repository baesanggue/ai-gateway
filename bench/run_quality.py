"""답변 품질 (#62, CLAUDE.md 6장): 같은 요청을 원문으로 보낸 답 vs 가명화 → 복원한 답.

질문: "이름·번호를 가려서 보내면 답이 나빠지지 않나요?"
- 대상: 테스트셋에서 개인정보가 있고(has_pii=1), 원본 값이 필요 없으며(needs_raw_value=0), LLM으로 가는(cheap|premium)
  요청 38개. 게이트웨이가 실제로 가려서 보내는 요청이다(원본 값이 필요한 요청은 막으므로 비교 대상이 아니다)
- 업종마다 그 업종 프리셋 + 시연용 사전 + 이름 인식 모델(게이트웨이와 같은 가명화, run_gateway와 같은 조건)
- A: 원문을 그대로 LLM에. B: 게이트웨이와 같이 가려서(토큰 유지 지시 포함) 보내고 답을 복원. 같은 등급의 같은 모델
- 채점: 고성능 모델이 두 답을 **어느 쪽이 가린 것인지 모른 채**, 순서를 무작위로 바꿔 1~5점 세 기준(요청 충족·정확성·
  자연스러움)과 더 나은 쪽(같음 포함)을 고른다. 자동 지표: 복원하지 못한 토큰, 답에 남은 토큰 모양
- 대조군: 원문 답을 한 번 더 받아 원문끼리도 같은 방식으로 채점한다. LLM 답은 매번 달라서, 가리지 않아도 한쪽을 고르는
  비율이 있다. 가명화의 영향은 그 비율과의 차이다
- 원문이 밖으로 나가는 유일한 측정이다. 테스트셋은 이름·번호가 모두 가상 값이다(#62)
- 사람이 직접 볼 10쌍은 review.md로 따로 뽑는다(CLAUDE.md 6장: LLM 채점 + 일부 수동 확인)

실행: uv run python bench/run_quality.py --out bench/results/quality-2026-11-12 [--limit N] [--seed 62]
"""
import argparse
import csv
import json
import random
import re
import statistics
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from gateway.config import load_settings  # noqa: E402
from gateway.llm.client import LLMClient, LLMError  # noqa: E402
from gateway.llm.cost import request_cost  # noqa: E402
from gateway.masking.masker import RuleMasker  # noqa: E402

TESTSET = ROOT / "data" / "testset.csv"
PRESET = {"T": "shop", "M": "medical", "F": None, "G": None}
TOKEN = re.compile(r"\[[^\[\]\d\s]{1,12}\d+\]")
CRITERIA = ("fulfil", "accuracy", "fluency")
CRITERIA_KO = {"fulfil": "요청 충족", "accuracy": "정확성", "fluency": "자연스러움"}
LABEL = {"original": "원문 답", "masked": "가린 답", "original2": "원문 답(2)"}

JUDGE = """너는 한국어 업무 답변을 평가하는 심사위원이다. 같은 요청에 대한 두 답변(답변 1, 답변 2)을 비교한다.
각 답변을 세 기준으로 1~5점(5가 가장 좋음) 매겨라.
- fulfil(요청 충족): 요청한 일을 했는가 (메일이면 메일, 요약이면 요약)
- accuracy(정확성): 요청에 나온 사람 이름·번호·주소·날짜 같은 값을 맞게 썼는가. 엉뚱한 이름, 대괄호 자리표시([고객1] 같은 것), 깨진 값, 사실과 다른 내용이 있으면 깎는다
- fluency(자연스러움): 한국어가 자연스럽고 읽기 쉬운가
그리고 어느 답이 전체적으로 나은지 고른다. 차이가 거의 없으면 same.
JSON 한 개만 출력하라: {"score_1": {"fulfil": n, "accuracy": n, "fluency": n}, "score_2": {...}, "better": "1"|"2"|"same", "reason": "한 문장"}"""


def settings_for(preset: str | None):
    """업종 프리셋 + 그 프리셋의 시연용 사전 (run_gateway와 같은 조건). 키는 .env."""
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    raw["preset"] = preset
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True)
    try:
        s = load_settings(Path(f.name), env_file=ROOT / ".env")
    finally:
        Path(f.name).unlink()
    s.config.masking.user_dictionary_path = str(ROOT / s.config.masking.example_dictionary_path)
    return s


def judge(llm: LLMClient, request: str, first: str, second: str) -> dict:
    msgs = [{"role": "developer", "content": JUDGE},
            {"role": "user", "content": f"[요청]\n{request}\n\n[답변 1]\n{first}\n\n[답변 2]\n{second}"}]
    r = llm.complete("premium", msgs)
    m = re.search(r"\{.*\}", r.text, flags=re.S)
    data = json.loads(m.group()) if m else {}
    data["_usage"] = r.usage
    return data


def blind_judge(llm: LLMClient, rnd: random.Random, request: str, x: str, y: str, names: tuple[str, str]):
    """x·y를 무작위 순서로 보여 채점한다(LLM 채점자는 앞에 놓인 답을 더 좋게 보는 경향이 있다).
    돌려주는 것: x 점수, y 점수, 나은 쪽(names[0]|names[1]|"same"|"?"), 이유, 채점 비용."""
    y_first = rnd.random() < 0.5
    j = judge(llm, request, *((y, x) if y_first else (x, y)))
    c = request_cost(j.pop("_usage"), llm.cfg.premium)
    sx, sy = (j.get("score_2"), j.get("score_1")) if y_first else (j.get("score_1"), j.get("score_2"))
    pick = {"1": names[1] if y_first else names[0], "2": names[0] if y_first else names[1], "same": "same"}
    return sx, sy, pick.get(str(j.get("better")), "?"), relabel(j.get("reason", ""), names, y_first), c


def relabel(reason: str, names: tuple[str, str], y_first: bool) -> str:
    """채점 이유의 "답변 1/2"(보여 준 자리)를 어느 쪽 답인지로 바꾼다. 자리로 두면 읽는 사람이 순서를 되짚어야 한다."""
    first, second = (names[1], names[0]) if y_first else names
    return (reason.replace("답변 1", "\x00").replace("답변 2", f"[{LABEL[second]}]")
            .replace("\x00", f"[{LABEL[first]}]"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--seed", type=int, default=62)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(args.seed)

    with TESTSET.open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f)
                if r["has_pii"] == "1" and r["needs_raw_value"] == "0" and r["expected_route"] in ("cheap", "premium")]
    rows = rows[: args.limit] if args.limit else rows

    presets = {k: settings_for(p) for k, p in PRESET.items()}
    maskers = {}
    llm = LLMClient.from_settings(presets["T"])
    cfg = presets["T"].config.llm
    out_rows, cost = [], 0.0
    t0 = time.perf_counter()
    for i, r in enumerate(rows, 1):
        industry = r["id"][0]
        if industry not in maskers:
            maskers[industry] = RuleMasker.from_config(presets[industry].config.masking)
        masker = maskers[industry]
        tier = r["expected_route"]
        msgs = [{"role": "user", "content": r["text"]}]
        rec = {"id": r["id"], "tier": tier, "request": r["text"]}
        try:
            a = llm.complete(tier, msgs)                                   # A: 원문
            masked = masker.mask(msgs)
            b = llm.complete(tier, masked.messages, masked=masked.has_pii)  # B: 가려서
            restored = masker.restore(b.text, masked.mapping)
            model = cfg.cheap if tier == "cheap" else cfg.premium
            cost += request_cost(a.usage, model) + request_cost(b.usage, model)
            sa, sb, better, reason, jc = blind_judge(llm, rnd, r["text"], a.text, restored.text, ("original", "masked"))
            cost += jc
            # 대조군: 원문 답을 한 번 더 받아 원문끼리 비교한다. 같은 요청도 LLM 답은 매번 달라서, 아무것도 안 바꿔도
            # 채점자가 한쪽을 고르는 비율이 있다. 가명화의 영향은 이 비율과의 차이로 본다
            a2 = llm.complete(tier, msgs)
            cost += request_cost(a2.usage, model)
            _, _, control, _, jc2 = blind_judge(llm, rnd, r["text"], a.text, a2.text, ("original", "original2"))
            cost += jc2
            rec.update({
                "masked_request": masked.messages[0]["content"], "answer_original": a.text,
                "answer_masked_raw": b.text, "answer_restored": restored.text,
                "unrestored": restored.unrestored, "tokens_left": len(TOKEN.findall(restored.text)),
                **{f"orig_{c}": (sa or {}).get(c) for c in CRITERIA}, **{f"mask_{c}": (sb or {}).get(c) for c in CRITERIA},
                "better": better, "reason": reason, "control": control, "error": "",
            })
        except (LLMError, ValueError, json.JSONDecodeError) as e:
            rec.update({"error": f"{type(e).__name__}: {e}"})
        out_rows.append(rec)
        print(f"[{i}/{len(rows)}] {r['id']} {rec.get('better', '-')} {rec.get('error', '')}", flush=True)
    llm.close()

    fields = ["id", "tier", "request", "masked_request", "answer_original", "answer_masked_raw", "answer_restored",
              "unrestored", "tokens_left", *[f"orig_{c}" for c in CRITERIA], *[f"mask_{c}" for c in CRITERIA],
              "better", "reason", "control", "error"]
    with (args.out / "answers.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(out_rows)
    write_summary(args.out, out_rows, cost, time.perf_counter() - t0)
    write_review(args.out, out_rows, rnd)


def _mean(vals) -> float:
    vals = [float(v) for v in vals if v not in (None, "")]
    return statistics.mean(vals) if vals else float("nan")


def write_summary(out: Path, rows: list[dict], cost: float, seconds: float) -> None:
    ok = [r for r in rows if not r.get("error")]
    n = len(ok)
    pref = {k: sum(r["better"] == k for r in ok) for k in ("original", "same", "masked")}
    lines = ["# 답변 품질 (#62) — 원문 vs 가명화 → 복원", "",
             f"대상: 테스트셋에서 가려서 보내는 요청 {len(rows)}개(개인정보 있음·원본 값 필요 없음·LLM 경로), 채점 성공 {n}개. "
             "업종 프리셋 + 시연용 사전 + 이름 인식 모델. 같은 등급의 같은 모델로 A(원문)·B(가려서 보내고 복원)를 만들고, "
             "고성능 모델이 어느 쪽이 가린 것인지 모른 채(순서 무작위) 채점했다.", "",
             "## 점수 (1~5, 평균)", "", "| 기준 | 원문 | 가명화 → 복원 | 차이 |", "|---|---|---|---|"]
    for c in CRITERIA:
        a, b = _mean(r[f"orig_{c}"] for r in ok), _mean(r[f"mask_{c}"] for r in ok)
        lines.append(f"| {CRITERIA_KO[c]} | {a:.2f} | {b:.2f} | {b - a:+.2f} |")
    if n:
        lines += ["", "## 어느 쪽이 나은가", "",
                  f"- 원문이 낫다 {pref['original']} · 같다 {pref['same']} · 가린 쪽이 낫다 {pref['masked']}",
                  f"- **같거나 가린 쪽이 나은 비율: {(pref['same'] + pref['masked']) / n:.0%}** ({pref['same'] + pref['masked']}/{n})"]
        ctrl = [r["control"] for r in ok]
        picked = sum(v in ("original", "original2") for v in ctrl)
        lines += ["", "## 대조군: 원문끼리 비교", "",
                  "같은 원문 요청을 한 번 더 보내 받은 답과 비교했다. 아무것도 가리지 않아도 채점자가 한쪽을 고르는 비율이다.", "",
                  f"- 같다 {len(ctrl) - picked} · 한쪽을 골랐다 {picked} → **원문끼리도 {picked / n:.0%}는 한쪽을 고른다**",
                  f"- 가명화 비교에서 한쪽을 고른 비율은 {(pref['original'] + pref['masked']) / n:.0%}"
                  f"(원문 쪽 {pref['original'] / n:.0%} · 가린 쪽 {pref['masked'] / n:.0%})"]
    lines += ["", "## 복원", "",
              f"- 복원하지 못한 토큰: {sum(int(r.get('unrestored') or 0) for r in ok)}개, "
              f"답에 토큰 모양이 남은 답: {sum(int(r.get('tokens_left') or 0) > 0 for r in ok)}개", "",
              "## 원문이 낫다고 본 답 (채점 이유)", ""]
    lines += [f"- {r['id']}: {r['reason']}" for r in ok if r["better"] == "original"] or ["- 없음"]
    errors = [r for r in rows if r.get("error")]
    if errors:
        lines += ["", "## 실패", ""] + [f"- {r['id']}: {r['error']}" for r in errors]
    lines += ["", f"비용 약 ${cost:.4f} (요청마다 답변 3개 + 채점 2회, {len(rows)}개), 걸린 시간 {seconds / 60:.1f}분. "
              "사람이 직접 본 결과는 review.md", ""]
    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def write_review(out: Path, rows: list[dict], rnd: random.Random) -> None:
    """사람이 직접 볼 10쌍: 원문이 낫다고 본 것을 먼저, 나머지는 무작위."""
    ok = [r for r in rows if not r.get("error")]
    worse = [r for r in ok if r["better"] == "original"]
    rest = [r for r in ok if r["better"] != "original"]
    picked = (worse + rnd.sample(rest, min(len(rest), max(0, 10 - len(worse)))))[:10]
    lines = ["# 직접 확인 (#62) — 10쌍", "", "각 쌍을 읽고 '가려서 받은 답이 실제로 써도 될 만한가'를 표시한다. 테스트셋 가상 값이다.", ""]
    for r in picked:
        lines += [f"## {r['id']} ({r['tier']}) — 채점: {r['better']}", "", f"**요청** {r['request']}", "",
                  f"**밖으로 나간 글** {r['masked_request']}", "", "**A. 원문으로 받은 답**", "", r["answer_original"], "",
                  "**B. 가려서 보내고 복원한 답**", "", r["answer_restored"], "",
                  "- [ ] 써도 된다   - [ ] 고쳐야 한다:", ""]
    (out / "review.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
