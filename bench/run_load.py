"""체험판 동시 접속 시험 (#115): 발표 날 반 전체가 QR을 보고 한꺼번에 보내는 경우를 잰다.

체험판 화면(admin/demo_page.py)과 같은 경로로 게이트웨이 `/v1/chat/completions`에 동시 요청 1·5·10·20·30개를 보낸다.
- 한 단계의 요청은 Barrier로 같은 순간에 출발시킨다(가장 몰리는 경우). 1개 단계는 문장 종류마다 한 번씩, 차례로 보낸다(기준)
- 문장: 체험판 '해 볼 것' 4가지(admin/beta.py)를 가상 값만 바꿔 쓴다. 판단 결과를 기억하는 캐시(decision/service.py)는
  가린 문장을 열쇠로 쓰므로, 가린 뒤에도 문장이 겹치지 않게 사소한 내용(물건·일수)을 바꾼다. 같은 문장을 그대로 치면 이보다 빠르다
- 화면의 시간 제한(demo_page.py, 120초)을 넘으면 테스터에게는 실패다
- 서버 쪽 지연(로컬 판단·클라우드 판단·전체)과 경로·비용은 응답 머리의 요청 ID로 요청 기록(data/gateway.db)에서 읽는다
- 안전 확인 두 가지
  - 요청 기록의 가린 문장에 보낸 가상 이름·번호·주소가 남아 있지 않은지(leaked)
  - 동시에 처리한 요청끼리 복원 대응표가 섞이지 않는지(mixed): 받은 답에 같은 단계의 다른 요청만 가진 값
    (요청마다 다른 전화번호·도로 번호)이 들어 있으면 섞인 것이다

실행 (게이트웨이·Ollaya를 **새로** 켠다: scripts\\stop.ps1 → scripts\\start.ps1. 판단 캐시는 게이트웨이 메모리에 있어서
같은 게이트웨이로 다시 돌리면 두 번째부터는 캐시를 써 로컬 판단을 재지 못한다. 요약에 캐시를 쓴 요청 수가 나온다):
  uv run python bench/run_load.py run --out bench/results/load-2026-11-13
  uv run python bench/run_load.py summary --out bench/results/load-2026-11-13
"""
import argparse
import csv
import json
import statistics
import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
from run_gateway import _p95, pc_spec  # noqa: E402

from gateway.config import load_settings  # noqa: E402
from gateway.storage.log import RequestLog  # noqa: E402

LEVELS = (1, 5, 10, 20, 30)
KINDS = ("faq", "mask", "raw", "free")          # 체험판 '해 볼 것' 1~4번
SCREEN_TIMEOUT = 120                             # demo_page.py의 OpenAI 클라이언트 timeout
CLIENT_TIMEOUT = 300                             # 화면 제한을 넘는 요청도 끝까지 재려고 더 길게 기다린다
FAKE_NAMES = ("이서연", "박지훈", "최유진", "정민호", "강하은", "장수빈", "임재원", "송다은", "권태윤", "조하린")
ITEMS = ("운동화", "니트", "텀블러", "가습기", "백팩", "이어폰")
FIELDS = ("level", "i", "kind", "status", "client_s", "request_id", "route", "local_ms", "local_cached",
          "cloud_ms", "server_ms", "cost_usd", "leaked", "own", "mixed", "error")


def sentence(kind: str, i: int) -> tuple[str, list[str]]:
    """i번째 요청의 문장과 가려져야 할 가상 값들. i가 다르면 가린 뒤의 문장도 다르다(faq는 판단 전에 끝나 상관없다)."""
    name, item, d = FAKE_NAMES[i % len(FAKE_NAMES)], ITEMS[i % len(ITEMS)], i + 1
    if kind == "faq":
        return "주문한 거 배송 언제 와요?", []
    if kind == "mask":
        phone = f"010-0000-{1000 + i:04d}"
        return f"{name} 고객({phone})에게 {item} 배송이 {d}일 늦어진 것 사과 메일 써줘", [name, phone]
    if kind == "raw":
        rrn = f"9{i % 10}0101-1{100000 + i:06d}"
        return f"주문 {d}건을 한 고객인데 이 주민번호 {rrn}로 고객 나이 계산해줘", [rrn]
    road = f"서울시 마포구 월드컵로 {10 + i}"
    return f"{name} 고객님 주소가 {road}로 바뀌어서 {item} 주문 {d}건의 배송지를 고쳤다고 안내 문자 써줘", [name, road]


def send(url: str, text: str) -> tuple[int, float, str | None, str, str]:
    """체험판 화면과 같은 요청 하나. (상태 코드, 걸린 초, 요청 ID, 받은 답, 오류)."""
    t0 = time.perf_counter()
    try:
        r = httpx.post(f"{url}/v1/chat/completions", timeout=CLIENT_TIMEOUT,
                       headers={"Authorization": "Bearer local-demo"},
                       json={"model": "gpt-6-luna", "messages": [{"role": "user", "content": text}]})
        answer = r.json()["choices"][0]["message"]["content"] or "" if r.status_code == 200 else ""
        return r.status_code, time.perf_counter() - t0, r.headers.get("x-gateway-request-id"), answer, ""
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as e:
        return 0, time.perf_counter() - t0, None, "", type(e).__name__


def cross_check(rows: list[dict]) -> None:
    """같은 단계의 답끼리 대응표가 섞였는지. 요청마다 하나뿐인 값(secrets의 마지막: 전화번호·도로 번호·주민번호)으로 본다.
    own: 자기 값이 답에 있다(LLM이 그 값을 답에 쓴 경우만, 1/0). mixed: 다른 요청만 가진 값이 답에 든 개수."""
    unique = [r["_secrets"][-1] if r["_secrets"] else None for r in rows]
    for r, mine in zip(rows, unique):
        answer = r.get("_answer", "")
        r["own"] = int(mine in answer) if mine and answer else ""
        r["mixed"] = sum(1 for other in unique if other and other != mine and other in answer)


def burst(url: str, level: int, start: int, kinds: list[str]) -> list[dict]:
    """kinds 개수만큼 스레드를 띄워 같은 순간에 보낸다. start는 문장 번호의 시작(단계끼리 문장이 겹치지 않게)."""
    gate = threading.Barrier(len(kinds))
    rows: list[dict] = [{} for _ in kinds]

    def one(k: int) -> None:
        text, secrets = sentence(kinds[k], start + k)
        gate.wait()
        status, secs, rid, answer, err = send(url, text)
        rows[k] = {"level": level, "i": start + k, "kind": kinds[k], "status": status, "client_s": round(secs, 2),
                   "request_id": rid or "", "error": err, "_secrets": secrets, "_answer": answer}

    threads = [threading.Thread(target=one, args=(k,)) for k in range(len(kinds))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    cross_check(rows)
    for r in rows:
        r.pop("_answer")                       # 답은 파일에 남기지 않는다(가상 값이지만 결과에 필요 없다)
    return rows


def server_side(row: dict, log: RequestLog) -> dict:
    """요청 기록에서 경로·지연·비용을 붙이고, 가린 문장에 가상 값이 남았는지 본다."""
    rec = log.get(row["request_id"]) if row["request_id"] else None
    secrets = row.pop("_secrets", [])
    if not rec:
        return {**row, "route": "", "local_ms": "", "local_cached": "", "cloud_ms": "", "server_ms": "",
                "cost_usd": "", "leaked": ""}
    j = rec["judgments"] or {}
    local, cloud = j.get("local") or {}, j.get("cloud") or {}
    masked = rec["text_masked"] or ""
    return {**row, "route": rec["route"], "local_ms": local.get("latency_ms", ""),
            "local_cached": int(bool(local.get("cached"))) if local else "", "cloud_ms": cloud.get("latency_ms", ""),
            "server_ms": rec["total_latency_ms"], "cost_usd": rec["cost_usd"],
            "leaked": sum(s in masked for s in secrets)}


def run(out: Path) -> None:
    settings = load_settings()
    g = settings.config.gateway
    url = f"http://{g.host}:{g.port}"                              # admin/ui.py gateway_url과 같다
    httpx.get(f"{url}/healthz", timeout=5).raise_for_status()
    log = RequestLog.from_config(settings.config.storage)
    out.mkdir(parents=True, exist_ok=True)
    for kind in ("faq", "mask"):                                   # 데우기 (집계에서 뺀다)
        send(url, sentence(kind, 999)[0])   # 999: 측정 문장(0부터)과 겹치지 않는 번호
    rows, n = [], 0
    for level in LEVELS:
        plans = [[k] for k in KINDS] if level == 1 else [[KINDS[j % len(KINDS)] for j in range(level)]]
        for kinds in plans:
            t0 = time.perf_counter()
            got = burst(url, level, n, kinds)
            n += len(kinds)
            print(f"level {level:>2}: {len(kinds)}개, 가장 늦은 요청 {max(r['client_s'] for r in got):.1f}초 "
                  f"(단계 전체 {time.perf_counter() - t0:.1f}초)", flush=True)
            rows += got
        time.sleep(3)                                              # 앞 단계의 뒷정리와 겹치지 않게
    rows = [server_side(r, log) for r in rows]
    with (out / "requests.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    meta = {"date": time.strftime("%Y-%m-%d %H:%M"), "pc": pc_spec(), "levels": LEVELS,
            "local_model": settings.config.decision.model, "cloud_model": settings.config.decision.cloud.model,
            "screen_timeout_s": SCREEN_TIMEOUT}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# ---------- 집계 ----------
def _num(rows: list[dict], key: str, skip_cached: bool = False) -> list[float]:
    return [float(r[key]) for r in rows
            if r.get(key) not in ("", None) and not (skip_cached and str(r.get("local_cached")) == "1")
            and not (key == "local_ms" and float(r[key]) == 0)]


def _ms(values: list[float]) -> str:
    return f"{statistics.mean(values) / 1000:.2f} / {_p95(values) / 1000:.2f} / {max(values) / 1000:.2f}" if values else "-"


def level_stats(rows: list[dict], level: int) -> dict:
    rs = [r for r in rows if int(r["level"]) == level]
    ok = [r for r in rs if int(r["status"]) == 200]
    secs = [float(r["client_s"]) for r in rs]
    return {"level": level, "n": len(rs), "ok": len(ok), "failed": len(rs) - len(ok),
            "over": sum(s > SCREEN_TIMEOUT for s in secs),
            "client": f"{statistics.mean(secs):.1f} / {_p95(secs):.1f} / {max(secs):.1f}" if secs else "-",
            "local": _ms(_num(rs, "local_ms", skip_cached=True)), "cloud": _ms(_num(rs, "cloud_ms")),
            "cost": sum(float(r["cost_usd"] or 0) for r in rs)}


def summary(out: Path) -> str:
    with (out / "requests.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    levels = sorted({int(r["level"]) for r in rows})
    lines = ["# 체험판 동시 접속 시험 (#115)", "",
             f"- 실행: {meta['date']}, {meta['pc'].get('cpu', '')}, RAM {meta['pc'].get('ram_gb', '')}GB, {meta['pc'].get('os', '')}",
             "- 체험판 화면과 같은 경로(`/v1/chat/completions`)로, 한 단계의 요청을 같은 순간에 보냈다. 1개 단계는 문장 종류마다 한 번씩 차례로",
             "- 문장은 '해 볼 것' 4가지(자주 묻는 질문 · 가리기 · 실제 값 필요 · 자유)를 고루 섞었다. 가린 뒤에도 문장이 겹치지 않게 해 판단 캐시를 쓰지 않았다",
             f"- 시간은 평균 / p95 / 최대(초). 화면 제한 {SCREEN_TIMEOUT}초를 넘으면 테스터에게는 실패다", "",
             "| 동시 요청 | 보냄 | 성공 | 실패 | 120초 넘음 | 화면이 받은 시간 | 로컬 판단 | 클라우드 판단 | LLM 비용 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for lv in levels:
        s = level_stats(rows, lv)
        lines.append(f"| {lv} | {s['n']} | {s['ok']} | {s['failed']} | {s['over']} | {s['client']} | {s['local']} | "
                     f"{s['cloud']} | ${s['cost']:.4f} |")
    top = [r for r in rows if int(r["level"]) == max(levels)]
    lines += ["", f"## 가장 몰린 단계({max(levels)}개)의 문장 종류별", "",
              "| 종류 | 개수 | 경로 | 화면이 받은 시간 (평균 / 최대) |", "|---|---|---|---|"]
    for kind in KINDS:
        ks = [r for r in top if r["kind"] == kind]
        if ks:
            secs = [float(r["client_s"]) for r in ks]
            routes = ", ".join(sorted({r["route"] or "?" for r in ks}))
            lines.append(f"| {kind} | {len(ks)} | {routes} | {statistics.mean(secs):.1f} / {max(secs):.1f} |")
    leaked = sum(int(r["leaked"] or 0) for r in rows)
    cached = sum(str(r["local_cached"]) == "1" for r in rows)
    mixed = sum(int(r.get("mixed") or 0) for r in rows)
    owns = [r for r in rows if r["route"] in ("cheap", "premium") and str(r.get("own", "")) in ("0", "1")]
    lines += ["", f"- 가린 문장에 남은 가상 값: {leaked}개 (요청 기록 기준)",
              f"- 다른 요청의 값이 섞인 답: {mixed}개. 자기 값이 복원된 답: {sum(r['own'] == '1' for r in owns)}/{len(owns)}개"
              " (LLM이 답한 요청 중. LLM이 그 값을 답에 쓰지 않으면 없다. 사과 메일에는 전화번호를 잘 쓰지 않는다)",
              f"- 로컬 판단 캐시를 쓴 요청: {cached}개"
              + (" — **로컬 판단 지연을 재지 못했다. 게이트웨이를 새로 켜고 다시 잰다**" if cached else ""),
              f"- LLM 비용 합계: ${sum(float(r['cost_usd'] or 0) for r in rows):.4f} (판단 API 비용은 따로, 아주 작다)", ""]
    text = "\n".join(lines)
    (out / "summary.md").write_text(text, encoding="utf-8")
    return text


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=("run", "summary"))
    p.add_argument("--out", type=Path, default=ROOT / "bench" / "results" / f"load-{time.strftime('%Y-%m-%d')}")
    a = p.parse_args()
    if a.cmd == "run":
        run(a.out)
    print(summary(a.out))


if __name__ == "__main__":
    main()
