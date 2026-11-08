"""게이트웨이 전체 벤치마크 (#59): 테스트셋 190행을 실제 게이트웨이(가명화 → FAQ → 로컬 판단 → 클라우드 판단 → LLM → 복원 → 로그)로
처리해 경로 정확도·비용 절감·지연을 잰다. 발표 숫자의 근거다 (CLAUDE.md 6장).

- 업종마다 그 업종 프리셋: T 쇼핑몰(shop), M 의료(medical). F 금융·G 공공은 프리셋이 없어 FAQ가 등록되지 않는다(따로 집계)
- 사전은 그 프리셋의 시연용 가상 사전, 이름 인식 모델은 실제 모델 (D23)
- 기대 경로: 테스트셋 `expected_route`는 기본 수준 기준이다(data/README.md). 엄격 수준은 기밀이면 차단, 아니면 같다
- 엄격 수준은 기본 수준이 받은 응답을 그대로 돌려주는 전송 계층으로 다시 돌린다. 같은 요청이면 같은 응답이라
  추가 호출·비용 없이 실제 게이트웨이 코드로 경로만 잰다. 캐시에 없는 요청이 있으면 실제로 부르고 개수를 적는다
- 안전 확인: 행마다 밖(api.openai.com)으로 나간 요청 바이트 전체에 원문의 개인정보 값(테스트셋 가상 이름, 정규식으로 찾은 번호)이 없는지
- 비용은 게이트웨이 로그의 추정 비용(D18 규칙)이다. 판단 API 비용은 입력 토큰 × $0.10/100만으로 따로 더한다

실행 (Ollaya를 먼저 띄운다. .env에 OPENAI_API_KEY):
  uv run python bench/run_gateway.py run --out bench/results/gateway-2026-11-08
  uv run python bench/run_gateway.py summary --out bench/results/gateway-2026-11-08
"""
import argparse
import csv
import json
import platform
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import run_masked as rm  # noqa: E402
from run_judges import NAMES  # noqa: E402

from gateway.config import load_settings  # noqa: E402
from gateway.masking.patterns import PATTERNS, normalize_text  # noqa: E402
from gateway.pipeline import Gateway  # noqa: E402
from gateway.storage.log import RequestLog  # noqa: E402

PRESET = {"T": "shop", "M": "medical", "F": None, "G": None}
ROUTES = ("template", "cheap", "premium", "block")
DECISION_PRICE_PER_1M = 0.10       # OpenAI Decisions 입력 가격, 출력 무료 (docs/llm-api-notes.md §1)


class ReplayTransport(httpx.BaseTransport):
    """실제 전송 계층을 감싸 요청·응답을 기록한다. replay=True면 같은 요청(주소+본문)에 기록한 응답을 돌려준다."""

    def __init__(self, real: httpx.BaseTransport | None = None):
        self.real = real or httpx.HTTPTransport()
        self.cache: dict[tuple[str, str, bytes], tuple[int, bytes, str]] = {}
        self.sent: list[httpx.Request] = []
        self.replay = False
        self.misses = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.sent.append(request)
        key = (request.method, str(request.url), request.content)
        if not (self.replay and key in self.cache):
            if self.replay:
                self.misses += 1
            response = self.real.handle_request(request)
            body = response.read()            # 압축을 푼 본문. 그래서 content-encoding 헤더는 넘기지 않는다
            response.close()
            self.cache[key] = (response.status_code, body, response.headers.get("content-type", "application/json"))
        status, body, content_type = self.cache[key]
        return httpx.Response(status, content=body, headers={"content-type": content_type}, request=request)

    def close(self) -> None:
        self.real.close()


def settings_for(preset: str | None, level: str, out: Path):
    """업종 프리셋 + 그 프리셋의 시연용 사전 + 벤치마크용 로그 DB."""
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    raw["preset"] = preset
    raw["security_level"] = level
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True)
    s = load_settings(Path(f.name), env_file=ROOT / ".env")
    Path(f.name).unlink()
    s.config.masking.user_dictionary_path = str(ROOT / s.config.masking.example_dictionary_path)
    s.config.storage.sqlite_path = str(out / f"gateway-{level}.db")
    return s


def raw_values(text: str) -> list[str]:
    """원문의 개인정보 값: 테스트셋 가상 이름 + 정규식으로 찾은 번호·이메일·주소."""
    norm = normalize_text(text)
    values = [n for n in NAMES if n in norm]
    for _, pattern in PATTERNS:
        values += [m.group() for m in pattern.finditer(norm)]
    return values


def expected_strict(row: dict) -> str:
    return "block" if row["is_confidential"] == "1" else row["expected_route"]


def run_level(level: str, rows: list[dict], transport: ReplayTransport, out: Path) -> list[dict]:
    records = []
    for prefix, preset in PRESET.items():
        settings = settings_for(preset, level, out)
        log = RequestLog.from_config(settings.config.storage)
        gateway = Gateway.from_settings(settings, transport=transport, log=log)
        for row in (r for r in rows if r["id"].startswith(prefix)):
            start = len(transport.sent)
            t0 = time.perf_counter()
            reply = gateway.handle([{"role": "user", "content": row["text"]}])
            wall = (time.perf_counter() - t0) * 1000
            outbound = b"".join(r.content for r in transport.sent[start:] if r.url.host == "api.openai.com")
            leaked = [v for v in raw_values(row["text"]) if v.encode() in outbound]
            logged = log.get(reply.request_id)
            j = logged["judgments"]
            records.append({
                "level": level, "id": row["id"], "industry": prefix, "preset": preset or "",
                "expected": row["expected_route"] if level == "basic" else expected_strict(row),
                "route": reply.route, "reasons": "|".join(logged["reasons"]), "faq_id": logged["faq_id"] or "",
                "model": reply.model or "", "error": reply.error or "",
                "prompt_tokens": logged["prompt_tokens"], "completion_tokens": logged["completion_tokens"],
                "reasoning_tokens": logged["reasoning_tokens"], "cost_usd": logged["cost_usd"],
                "baseline_cost_usd": logged["baseline_cost_usd"], "decision_input_tokens": logged["decision_input_tokens"],
                "decision_latency_ms": logged["decision_latency_ms"], "total_latency_ms": logged["total_latency_ms"],
                "wall_ms": round(wall, 1), "pii_counts": json.dumps(logged["pii_counts"], ensure_ascii=False),
                "unrestored": logged["unrestored_tokens"], "external_requests": sum(
                    r.url.host == "api.openai.com" for r in transport.sent[start:]),
                "leaked": "|".join(leaked), "judgments": json.dumps(j, ensure_ascii=False),
                "text_logged": logged["text_masked"] or "", "reply": reply.text})
        gateway.close()
        print(f"{level} {prefix}({preset or '프리셋 없음'}) 끝")
    return records


def pc_spec() -> dict:
    def ps(cmd: str) -> str:
        try:
            return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True,
                                  timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    return {"os": platform.platform(), "python": platform.python_version(),
            "cpu": ps("(Get-CimInstance Win32_Processor).Name"),
            "ram_gb": ps("[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB)"),
            "gpu": ps("(Get-CimInstance Win32_VideoController).Name -join ', '"),
            "note": "판단·이름 인식은 CPU. GPU는 쓰지 않는다"}


def run(out: Path) -> None:
    rows = rm.load_rows()
    transport = ReplayTransport()
    basic = run_level("basic", rows, transport, out)
    transport.replay = True
    strict = run_level("strict", rows, transport, out)
    transport.close()
    rm._write(out / "requests.csv", basic + strict)
    meta = {"date": time.strftime("%Y-%m-%d"), "pc": pc_spec(), "strict_replay_misses": transport.misses,
            "config": {k: v for k, v in yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")).items()
                       if k in ("decision", "llm")}}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# ---------- 집계 ----------
def _p95(values: list[float]) -> float:
    v = sorted(values)
    return v[min(len(v) - 1, int(len(v) * 0.95))] if v else 0.0


def accuracy_lines(rows: list[dict], level: str) -> list[str]:
    rs = [r for r in rows if r["level"] == level]
    hit = [r for r in rs if r["route"] == r["expected"]]
    faq_missing = [r for r in rs if r["expected"] == "template" and not r["preset"]]
    out = [f"### {level}: 경로 정확도", "",
           f"- 전체 {rm.rate(len(hit), len(rs))}. FAQ를 등록하지 않은 업종(F·G)의 템플릿 행 {len(faq_missing)}개를 빼면 "
           f"{rm.rate(len(hit), len(rs) - len(faq_missing))}", ""]
    out += ["| 업종 | 정확도 |", "|---|---|"]
    for ind in PRESET:
        ir = [r for r in rs if r["industry"] == ind]
        out.append(f"| {ind} ({PRESET[ind] or '프리셋 없음'}) | {rm.rate(sum(r['route'] == r['expected'] for r in ir), len(ir))} |")
    out += ["", "| 기대 \\ 실제 | " + " | ".join(ROUTES) + " |", "|---|" + "---|" * len(ROUTES)]
    for e in ROUTES:
        c = Counter(r["route"] for r in rs if r["expected"] == e)
        out.append(f"| {e} | " + " | ".join(str(c[a]) for a in ROUTES) + " |")
    wrong = [r for r in rs if r["route"] != r["expected"] and r not in faq_missing]
    out += ["", f"기대와 다른 행 ({len(wrong)}개, F·G FAQ 미등록 제외):", ""]
    out += [f"- {r['id']} 기대 {r['expected']} → {r['route']} ({r['reasons']}) — {r['text_logged'] or '(로그에 문장 없음)'}"
            for r in wrong] or ["- 없음"]
    return out + [""]


def summary(out: Path) -> None:
    with (out / "requests.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    basic = [r for r in rows if r["level"] == "basic"]
    llm_cost = sum(float(r["cost_usd"]) for r in basic)
    decision_cost = sum(int(r["decision_input_tokens"]) for r in basic) * DECISION_PRICE_PER_1M / 1e6
    baseline = sum(float(r["baseline_cost_usd"]) for r in basic)
    judged = [float(r["decision_latency_ms"]) for r in basic if float(r["decision_latency_ms"]) > 0]
    total = [float(r["total_latency_ms"]) for r in basic]
    leaks = [r for r in rows if r["leaked"]]
    routes = Counter(r["route"] for r in basic)
    lines = ["# 게이트웨이 전체 벤치마크 (#59)", "",
             f"테스트셋 190행을 실제 게이트웨이로 처리했다 ({meta['date']}). 업종별 프리셋(T 쇼핑몰, M 의료, F·G 없음), "
             "시연용 사전 + 이름 인식 모델. 기대 경로는 기본 수준 라벨, 엄격 수준은 기밀이면 차단.", "",
             "## 한눈에", "",
             "| 지표 | 값 |", "|---|---|",
             f"| 경로 정확도 (기본) | {rm.rate(sum(r['route'] == r['expected'] for r in basic), len(basic))} |",
             f"| 경로 분포 (기본) | 템플릿 {routes['template']} · 저렴 {routes['cheap']} · 고성능 {routes['premium']} · 차단 {routes['block']} |",
             f"| 비용: 게이트웨이 (LLM + 판단 API) | ${llm_cost + decision_cost:.4f} (LLM ${llm_cost:.4f} + 판단 ${decision_cost:.4f}) |",
             f"| 비용: 전부 고성능 모델로 보냈다면 (D18 추정) | ${baseline:.4f} |",
             f"| 절감률 | {1 - (llm_cost + decision_cost) / baseline:.0%} |" if baseline else "| 절감률 | — |",
             f"| 판단 지연 (판단을 거친 {len(judged)}행) | 평균 {sum(judged) / len(judged):.0f}ms, p95 {_p95(judged):.0f}ms |" if judged else "",
             f"| 전체 지연 (LLM 포함) | 평균 {sum(total) / len(total):.0f}ms, p95 {_p95(total):.0f}ms |",
             f"| 밖으로 나간 요청에 원본 값 | **{len(leaks)}행** |",
             f"| 복원하지 못한 토큰 | {sum(int(r['unrestored']) for r in basic)}개 |",
             f"| 엄격 수준 재실행에서 캐시에 없던 요청 | {meta['strict_replay_misses']}건 |", "",
             "## 경로", "", *accuracy_lines(rows, "basic"), *accuracy_lines(rows, "strict"),
             "## 실행 환경", "", *[f"- {k}: {v}" for k, v in meta["pc"].items()], ""]
    (out / "summary.md").write_text("\n".join(line for line in lines if line is not None), encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["run", "summary"])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.step == "run":
        run(args.out)
    summary(args.out)


if __name__ == "__main__":
    main()
