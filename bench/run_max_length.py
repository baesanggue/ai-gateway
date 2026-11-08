"""판단 모델이 읽는 최대 길이 (#39, D19): 로컬 판단 모델(Ollaya + config의 model)에 글을 늘려 가며 보내 거부되는 경계를 찾고,
그보다 긴 요청을 게이트웨이가 D19대로 처리하는지 확인한다.

- 질문은 게이트웨이가 실제로 묻는 로컬 질문(decision.local_questions)이다
- 글 두 종류: 한국어 문장, 숫자·영문이 섞인 업무 문서 (글자당 토큰 수가 다르다)
- 경계는 두 배씩 늘려 처음 거부되는 길이를 찾은 뒤 이진 탐색으로 좁힌다 (글자 단위 ±1%까지)
- 게이트웨이 확인: 로컬 판단 서버만 실제로 부르고 클라우드 판단·LLM은 가짜(외부 호출·비용 없음)로,
  경계를 넘는 요청을 기본·엄격 수준에서 보낸다. 기대: 엄격은 차단(local_error), 기본은 진행하고 로그에 로컬 판단 오류

실행 (Ollaya를 먼저 띄운다): uv run python bench/run_max_length.py --out bench/results/max-length-2026-11-08
"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
import run_masked as rm  # noqa: E402

from gateway.config import load_settings  # noqa: E402
from gateway.decision.engines import systemone_questions  # noqa: E402
from gateway.decision.questions import QUESTIONS  # noqa: E402
from gateway.pipeline import Gateway  # noqa: E402
from gateway.storage.log import RequestLog  # noqa: E402
from tests.fakes import FakeUpstream, test_settings  # noqa: E402

TEXTS = {
    "korean": "고객 문의 내역을 정리해 주세요. 지난주 배송 지연으로 불편을 드린 고객님께 사과 안내를 드렸고, 교환 요청은 내일 처리합니다. ",
    "mixed": "주문번호 A-2026-1107, 수량 3개, 금액 45,000원, 상태 shipped, 담당 CS팀 2파트, 처리 기한 11/12 18:00. ",
}
MAX_CHARS = 400_000


def body_for(settings, text: str) -> dict:
    d = settings.config.decision
    return {"model": d.model, "state": text,
            "questions": systemone_questions({q: QUESTIONS[q] for q in d.local_questions})}


def ask(client: httpx.Client, settings, text: str) -> dict:
    t0 = time.perf_counter()
    r = client.post("/v1/systemone", json=body_for(settings, text))
    ms = (time.perf_counter() - t0) * 1000
    try:
        res = r.json()
    except ValueError:
        res = {}
    return {"status": r.status_code, "ms": round(ms, 1), "input_tokens": res.get("usage", {}).get("input_tokens"),
            "error": res.get("error") if r.status_code != 200 else None}


def make(base: str, n: int) -> str:
    return (base * (n // len(base) + 1))[:n]


def boundary(client, settings, base: str) -> dict:
    """거부되지 않는 가장 긴 글자 수. 두 배씩 늘린 뒤 이진 탐색."""
    lo, lo_res, n = 0, None, 200
    while n <= MAX_CHARS:
        res = ask(client, settings, make(base, n))
        if res["status"] != 200:
            hi, hi_res = n, res
            break
        lo, lo_res, n = n, res, n * 2
    else:
        return {"max_ok_chars": lo, "note": f"{MAX_CHARS}자까지 거부되지 않음", "ok": lo_res}
    while hi - lo > max(10, lo // 100):
        mid = (lo + hi) // 2
        res = ask(client, settings, make(base, mid))
        if res["status"] == 200:
            lo, lo_res = mid, res
        else:
            hi, hi_res = mid, res
    return {"max_ok_chars": lo, "first_fail_chars": hi, "ok": lo_res, "fail": hi_res}


def gateway_check(settings_real, over: int, out: Path) -> list[dict]:
    """경계를 넘는 요청을 게이트웨이로. 로컬 판단 서버는 실제, 클라우드·LLM은 가짜."""
    real, fake = httpx.HTTPTransport(), httpx.MockTransport(FakeUpstream())
    local = httpx.URL(settings_real.config.decision.base_url)

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.host == local.host and request.url.port == local.port:
            r = real.handle_request(request)
            r.read()
            return httpx.Response(r.status_code, content=r.content, headers={"content-type": "application/json"})
        return fake.handle_request(request)

    text = make(TEXTS["korean"], over)
    records = []
    for level in ("basic", "strict"):
        s = test_settings(out)                          # 테스트 키·시연 사전 (가짜 클라우드라 실제 키가 필요 없다)
        s.config.security_level = level
        s.config.decision.base_url = settings_real.config.decision.base_url
        log = RequestLog(out / f"max-length-{level}.db")
        gw = Gateway.from_settings(s, transport=httpx.MockTransport(route), log=log)
        reply = gw.handle([{"role": "user", "content": text}])
        row = log.get(reply.request_id)
        local_j = row["judgments"].get("local", {})
        records.append({"level": level, "chars": len(text), "route": reply.route, "reasons": row["reasons"],
                        "local_error": local_j.get("error"), "cloud_called": "cloud" in row["judgments"],
                        "text_logged": bool(row["text_masked"])})
        gw.close()
    return records


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    d = settings.config.decision
    client = httpx.Client(base_url=d.base_url, timeout=300)
    ask(client, settings, "예열")
    result = {"model": d.model, "questions": d.local_questions, "texts": {}}
    for name, base in TEXTS.items():
        b = boundary(client, settings, base)
        lengths = [n for n in (100, 500, 1_000, 2_000, 4_000) if n < b["max_ok_chars"]] + [b["max_ok_chars"]]
        b["latency"] = {n: ask(client, settings, make(base, n))["ms"] for n in lengths}
        result["texts"][name] = b
        print(name, json.dumps(b, ensure_ascii=False))
    over = int(result["texts"]["korean"].get("first_fail_chars") or 0)
    result["gateway"] = gateway_check(settings, over, args.out) if over else []
    print("gateway", json.dumps(result["gateway"], ensure_ascii=False))
    (args.out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    client.close()


if __name__ == "__main__":
    main()
