"""가명 토큰 유지 시험 (#9).

가명화된 요청에 "토큰을 그대로 써라" 지시(llm.placeholder_instruction)를 붙였을 때와 안 붙였을 때,
LLM 답변에 이름 토큰([고객1] 등)이 남는지 잰다. 토큰이 사라지면 응답 복원(시연 4번)이 화면에 보이지 않는다.
- 대상: 테스트셋에서 개인정보가 있고(has_pii=1), 원본 값이 필요 없으며(needs_raw_value=0),
  LLM으로 가는(cheap|premium) 요청 중 가명화 후 이름 토큰이 있는 문장
- 입력은 측정용 가명화 근사(run_judges.mask_for_routing)
- 함께 기록: 등급별 입력·출력 토큰 → "전부 고성능이었다면" 비용 추정의 근거 (gateway/llm/cost.py)

실행: uv run python bench/placeholder_check.py --out-dir bench/results/placeholder-<날짜>
"""
import argparse
import csv
import json
import re
import statistics
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_judges import mask_for_routing  # noqa: E402

from gateway.config import load_settings  # noqa: E402
from gateway.llm.client import LLMClient, LLMError  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTSET = ROOT / "data" / "testset.csv"
TOKEN = re.compile(r"\[[^\]\d\[]+\d+\]")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()

    with TESTSET.open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f)
                if r["has_pii"] == "1" and r["needs_raw_value"] == "0" and r["expected_route"] in ("cheap", "premium")]
    rows = [(r, mask_for_routing(r["text"])) for r in rows]
    rows = [(r, m) for r, m in rows if "[고객" in m]

    client = LLMClient.from_settings(load_settings())
    records = []
    try:
        for row, masked in rows:
            tokens = set(TOKEN.findall(masked))
            names = {t for t in tokens if t.startswith("[고객")}
            for tier in ("cheap", "premium"):
                for instruction in (False, True):
                    rec = {"id": row["id"], "expected_route": row["expected_route"], "tier": tier,
                           "instruction": instruction, "input": masked, "input_chars": len(masked)}
                    try:
                        r = client.complete(tier, [{"role": "user", "content": masked}], masked=instruction)
                    except LLMError as e:
                        records.append(rec | {"error": str(e)})
                        print(row["id"], tier, instruction, "ERROR", e, flush=True)
                        continue
                    out = set(TOKEN.findall(r.text))
                    rec |= {"error": "", "name_kept": names <= out, "all_kept": tokens <= out,
                            "invented": ",".join(sorted(out - tokens)),
                            "prompt_tokens": r.usage.prompt_tokens, "completion_tokens": r.usage.completion_tokens,
                            "reasoning_tokens": r.usage.reasoning_tokens, "latency_ms": round(r.latency_ms),
                            "finish_reason": r.finish_reason, "output": r.text}
                    records.append(rec)
                    print(row["id"], tier, instruction, "name_kept" if rec["name_kept"] else "NAME LOST",
                          rec["invented"], flush=True)
    finally:
        client.close()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.out_dir / f"{date.today().isoformat()}-placeholder"
    fields = list(dict.fromkeys(k for r in records for k in r))
    with open(f"{stem}.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(records)

    ok = [r for r in records if not r["error"]]
    summary = {"rows": len(rows), "calls": len(records), "errors": len(records) - len(ok), "conditions": {}}
    for tier in ("cheap", "premium"):
        for instruction in (False, True):
            rs = [r for r in ok if r["tier"] == tier and r["instruction"] == instruction]
            summary["conditions"][f"{tier}/{'지시' if instruction else '지시 없음'}"] = {
                "n": len(rs),
                "name_kept": f"{sum(r['name_kept'] for r in rs)}/{len(rs)}",
                "all_kept": f"{sum(r['all_kept'] for r in rs)}/{len(rs)}",
                "invented_rows": sorted(r["id"] for r in rs if r["invented"]),
                "prompt_tokens_mean": round(statistics.mean(r["prompt_tokens"] for r in rs), 1),
                "completion_tokens_mean": round(statistics.mean(r["completion_tokens"] for r in rs), 1),
                "latency_ms_mean": round(statistics.mean(r["latency_ms"] for r in rs)),
            }
    # 비용 추정 근거: 같은 문장·같은 조건(지시 있음)에서 고성능 출력 토큰 / 저렴한 출력 토큰
    by = {(r["id"], r["tier"]): r for r in ok if r["instruction"]}
    ratios = [by[i, "premium"]["completion_tokens"] / by[i, "cheap"]["completion_tokens"]
              for i in {r["id"] for r in ok} if (i, "premium") in by and (i, "cheap") in by]
    plain = [r for r in ok if not r["instruction"]]
    summary["premium_over_cheap_output_ratio"] = {"mean": round(statistics.mean(ratios), 2),
                                                  "median": round(statistics.median(ratios), 2), "n": len(ratios)}
    # 지시 없는 요청의 입력 토큰 / 글자 수 (메시지 형식 오버헤드 포함)
    summary["prompt_tokens_per_char"] = round(statistics.mean(r["prompt_tokens"] / r["input_chars"] for r in plain), 3)
    Path(f"{stem}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8", newline="\n")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
