"""완전 로컬 수준 측정 (#136, D34): 테스트셋에서 원본 값이 필요한 행(needs_raw_value=1)을 완전 로컬 수준의 실제 게이트웨이로
처리해 경로·지연·답을 기록한다. 엄격·기본 수준에서는 이 행들이 막힌다(11/08 측정, bench/results/gateway-2026-11-08).

- 업종마다 그 업종 프리셋과 시연용 가상 사전, 이름 인식 모델은 실제 모델 (run_gateway.py와 같은 구성)
- 로컬 판단은 Ollaya, 클라우드 판단은 OpenAI Decisions(가명화된 문장), 로컬 LLM은 이 PC의 Ollama(config.yaml의 llm.local)
- 안전 확인: 행마다 밖(api.openai.com)으로 나간 요청 바이트에 원문의 개인정보 값이 없는지 (run_gateway.raw_values)
- 지연: 로컬 LLM 호출 시간만 따로 잰다. 첫 호출은 모델을 메모리에 올리는 시간이 들어가 따로 적는다
- 테스트셋은 측정에만 쓴다. 로컬 LLM 지시문은 이 측정 전에 테스트셋 밖 문장으로 정했다 (11/23)

실행 (Ollaya와 Ollama를 먼저 띄운다. .env에 OPENAI_API_KEY):
  uv run python bench/run_local_mode.py run --out bench/results/local-mode-2026-11-23
  uv run python bench/run_local_mode.py summary --out bench/results/local-mode-2026-11-23
"""
import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import run_gateway as rg  # noqa: E402
import run_masked as rm  # noqa: E402

from gateway.pipeline import Gateway  # noqa: E402
from gateway.storage.log import RequestLog  # noqa: E402


class TimedLocal:
    """로컬 LLM 클라이언트를 감싸 호출마다 걸린 시간·토큰을 남긴다 (게이트웨이 코드는 그대로)."""

    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def complete(self, messages, **kw):
        t0 = time.perf_counter()
        try:
            result = self.inner.complete(messages, **kw)
        except Exception as e:
            self.calls.append({"ms": (time.perf_counter() - t0) * 1000, "error": type(e).__name__})
            raise
        self.calls.append({"ms": (time.perf_counter() - t0) * 1000, "error": "",
                           "completion_tokens": result.usage.completion_tokens, "finish": result.finish_reason})
        return result

    def close(self):
        self.inner.close()


def run(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rows = [r for r in rm.load_rows() if r["needs_raw_value"] == "1"]
    transport = rg.ReplayTransport()          # 기록만 한다 (replay=False)
    records, first = [], True
    for prefix, preset in rg.PRESET.items():
        settings = rg.settings_for(preset, "local", out)
        log = RequestLog.from_config(settings.config.storage)
        gateway = Gateway.from_settings(settings, transport=transport, log=log)
        timed = TimedLocal(gateway.local_llm)
        gateway.local_llm = timed
        for row in (r for r in rows if r["id"].startswith(prefix)):
            start, calls = len(transport.sent), len(timed.calls)
            reply = gateway.handle([{"role": "user", "content": row["text"]}])
            outbound = b"".join(r.content for r in transport.sent[start:] if r.url.host == "api.openai.com")
            leaked = [v for v in rg.raw_values(row["text"]) if v.encode() in outbound]
            logged = log.get(reply.request_id)
            call = timed.calls[calls] if len(timed.calls) > calls else {}
            records.append({
                "id": row["id"], "industry": prefix, "preset": preset or "", "route": reply.route,
                "reasons": "|".join(logged["reasons"]), "model": reply.model or "",
                "local_ms": round(call.get("ms", 0), 1) if call else "", "cold": "1" if call and first else "",
                "local_error": call.get("error", ""), "completion_tokens": call.get("completion_tokens", ""),
                "finish": reply.finish_reason, "total_latency_ms": logged["total_latency_ms"],
                "decision_latency_ms": logged["decision_latency_ms"],
                "external_requests": sum(r.url.host == "api.openai.com" for r in transport.sent[start:]),
                "leaked": "|".join(leaked), "text": row["text"], "reply": reply.text})
            first = first and not call
        gateway.close()
        print(f"local {prefix}({preset or '프리셋 없음'}) 끝")
    transport.close()
    rm._write(out / "requests.csv", records)
    meta = {"date": time.strftime("%Y-%m-%d"), "pc": rg.pc_spec() | {"note": "판단·이름 인식은 CPU, 로컬 LLM(Ollama)은 GPU"},
            "local_llm": rg.settings_for(None, "local", out).config.llm.local.model_dump()}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def summary(out: Path) -> None:
    with (out / "requests.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    routes = Counter(r["route"] for r in rows)
    local = [r for r in rows if r["route"] == "local"]
    warm = [float(r["local_ms"]) for r in local if r["local_ms"] and not r["cold"]]
    cold = [float(r["local_ms"]) for r in local if r["cold"]]
    leaked = [r for r in rows if r["leaked"]]
    lines = [f"# 완전 로컬 수준 측정 ({meta['date']}, #136)", "",
             f"테스트셋에서 원본 값이 필요한 {len(rows)}행(`needs_raw_value=1`)을 완전 로컬 수준의 실제 게이트웨이로 처리했다. "
             f"로컬 LLM: `{meta['local_llm']['model']}` (Ollama, 생각 모드 {meta['local_llm']['reasoning_effort']}). "
             f"PC: {meta['pc'].get('cpu', '')}, {meta['pc'].get('gpu', '')}, RAM {meta['pc'].get('ram_gb', '')}GB.", "",
             "## 경로", "", "| 경로 | 행 |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in routes.most_common()]
    lines += ["", f"- 로컬 LLM이 답한 행: {rm.rate(len(local), len(rows))}. 엄격·기본 수준에서는 이 행들이 원본 필요로 막혔다(11/08 측정)."]
    others = [r for r in rows if r["route"] != "local"]
    lines += [f"- 로컬로 가지 않은 행 ({len(others)}개):"] + [
        f"  - {r['id']} → {r['route']} ({r['reasons']})" for r in others] if others else ["- 로컬로 가지 않은 행: 없음"]
    lines += ["", "## 안전", "",
              f"- 밖(api.openai.com)으로 나간 요청에 원문의 개인정보 값이 들어간 행: **{len(leaked)}개**"
              + ("" if not leaked else " — " + ", ".join(r["id"] for r in leaked)),
              f"- 로컬 LLM 오류: {sum(bool(r['local_error']) for r in rows)}개", "",
              "## 지연 (로컬 LLM 호출만)", ""]
    if warm:
        lines += [f"- 평균 {sum(warm) / len(warm) / 1000:.1f}초, p95 {rg._p95(warm) / 1000:.1f}초, 최대 {max(warm) / 1000:.1f}초 "
                  f"({len(warm)}건, 모델이 올라와 있을 때)"]
    if cold:
        lines += [f"- 첫 호출(모델을 메모리에 올리는 시간 포함): {cold[0] / 1000:.1f}초"]
    total = [float(r["total_latency_ms"]) for r in local]
    if total:
        lines += [f"- 게이트웨이 전체(가리기·판단·로컬 LLM): 평균 {sum(total) / len(total) / 1000:.1f}초, p95 {rg._p95(total) / 1000:.1f}초"]
    lines += ["", "## 답 (직접 읽기용)", "", "답은 `requests.csv`의 reply 열에 있다. 값은 모두 테스트셋의 가상 값이다.", ""]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print((out / "summary.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "summary"])
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    (run if a.cmd == "run" else summary)(a.out)
