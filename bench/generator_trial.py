"""생성 모델 시험 (#21): 파인튜닝용 한국어 요청 문장을 어느 로컬 모델로 만들지 고른다.

같은 조건(업종 × 라벨) 8개를 세 모델에 주고 조건마다 10문장씩 만들게 한 뒤, 코드로 잴 수 있는 것을 잰다.
- 형식: JSON으로 정확히 10개를 돌려줬는가
- 한국어: 한글 비율, 한자·가나가 섞였는가
- 조건: 개인정보 값이 있어야 할 조건에는 있고, 없어야 할 조건에는 없는가 (측정용 정규식 run_judges.PATTERNS)
- 다양성: 같은 조건 안에서 거의 같은 문장이 반복되는가
- 누수: 테스트셋 문장과 거의 같은가. 프롬프트에 테스트셋 문장을 넣지 않는다 (학습 데이터로 새지 않게)
- 속도: 초당 출력 토큰, GPU에 올라간 비율
문장이 자연스러운지, "원본 필요"·"기밀" 같은 조건을 정말 지켰는지는 사람이 읽고 판단한다 (side-by-side.md).

실행: uv run python bench/generator_trial.py --out-dir bench/results/generator-trial-<날짜>
전제: Ollama가 127.0.0.1:11434에서 돌고, 모델을 받아 둔 상태 (ollama pull <모델>)
"""
import argparse
import csv
import json
import re
import statistics
import sys
import time
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_judges import PATTERNS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTSET = ROOT / "data" / "testset.csv"
OLLAMA = "http://127.0.0.1:11434"
MODELS = ["gemma4:12b", "gpt-oss:20b", "mistral-small3.2:24b"]   # qwen3.8:27b는 보안 보고로 제외 (docs/llm-api-notes.md §5)
N = 10

SYSTEM = ("너는 한국어 업무용 AI 챗봇에 들어오는 요청을 분류하는 모델의 학습 데이터를 만든다. "
          "실제 사람이 챗봇 입력창에 칠 법한 자연스러운 한국어 요청 문장을 만든다.")
RULES = f"""규칙:
- 정확히 {N}개를 만든다. 서로 상황이 겹치지 않게 하고, 존댓말·반말, 짧은 문장·긴 문장을 섞는다.
- 요청 문장만 쓴다. 번호, 따옴표, 설명, 라벨을 붙이지 않는다.
- 사람 이름, 전화번호, 주소, 계좌번호 같은 값은 모두 지어낸 가상 값을 쓴다.
- 답은 {{"requests": ["요청1", ..., "요청{N}"]}} 형식의 JSON 하나만 출력한다."""
SCHEMA = {"type": "object", "required": ["requests"],
          "properties": {"requests": {"type": "array", "items": {"type": "string"}}}}

# 조건: (id, 업종·상황, 만들 요청, 측정용 정규식으로 개인정보 값이 잡혀야 하는가)
SPECS = [
    ("simple_faq", "쇼핑몰 고객센터",
     "고객이 묻는 짧고 단순한 문의. 배송 일정, 반품·교환 방법, 영업시간, 결제 수단처럼 FAQ 하나로 답할 수 있는 것. "
     "개인정보는 넣지 않는다.", False),
    ("complex_task", "사내 업무",
     "직원이 AI에게 맡기는 복잡한 업무 요청. 여러 조건이나 자료를 비교·분석하거나 긴 문서를 써야 하는 것"
     "(보고서 초안, 비교표, 기획안 검토 등). 개인정보는 넣지 않는다.", False),
    ("pii_maskable", "병원 원무과",
     "환자의 이름과 휴대전화 번호(010-XXXX-XXXX 형식)가 실제처럼 보이는 가상 값으로 문장에 들어 있는 요청. "
     "다만 이름과 번호를 가려도 AI가 답할 수 있는 일이어야 한다(예약 안내 문자 작성, 검사 결과 안내 메일 작성 등). "
     "[환자1]이나 '환자1' 같은 가명 표기는 쓰지 않는다.", True),
    ("needs_raw", "은행·카드사 상담",
     "고객의 주민등록번호, 계좌번호, 카드번호 중 하나 이상이 하이픈을 포함한 실제 형식으로 문장에 들어 있고, 문장에 있는 그 번호만 "
     "보고 계산하거나 비교해야 답할 수 있는 요청(주민번호로 나이·성별 계산, 계좌번호 자릿수 확인, 두 번호가 같은지 비교 등). "
     "카드 한도·결제 금액·유효기간처럼 회사 시스템을 조회해야 하는 요청은 만들지 않는다.", True),
    ("hard_negative_pii", "구청 민원실",
     "주민등록번호·전화번호·계좌번호 같은 개인정보 '단어'는 나오지만 실제 값은 전혀 없는 요청(발급 절차, 변경 방법 문의 등). "
     "숫자로 된 개인정보 값은 절대 넣지 않는다.", False),
    ("confidential", "사내 업무",
     "외부에 알려지면 안 되는 회사 기밀의 구체적인 내용(수치, 회사명, 조건)이 문장 안에 직접 들어 있는 요청"
     "(미공개 실적 수치, 인수합병 대상과 금액, 인사평가 결과, 거래처 단가·계약 조건 등). "
     "기밀의 주제만 말하고 내용은 없는 요청은 만들지 않는다. 개인 연락처는 넣지 않는다.", False),
    ("risky", "업무용 AI 챗봇",
     "AI가 받은 지시를 무시하게 하거나(프롬프트 인젝션), 시스템 프롬프트나 다른 고객 정보를 빼내려 하거나, "
     "불법·유해한 일을 시키려는 요청. 해를 끼칠 구체적인 방법은 쓰지 말고 요청 문장만 만든다.", False),
    ("off_topic", "쇼핑몰 고객센터 챗봇",
     "고객센터 업무와 전혀 관계없는 잡담이나 질문(날씨, 연애 상담, 숙제, 운세 등). 개인정보는 넣지 않는다.", False),
]

DATE = re.compile(r"(19|20)\d{2}-\d{1,2}-\d{1,2}")   # 계좌번호 정규식에 날짜가 걸리는 것을 뺀다
FOREIGN = re.compile(r"[぀-ヿ一-鿿]")  # 가나, 한자


def pii_found(text: str) -> list[str]:
    found = []
    for kind, pat in PATTERNS:
        for m in pat.finditer(text):
            if kind == "계좌번호" and DATE.fullmatch(m.group()):
                continue
            found.append(kind)
            text = text.replace(m.group(), " ")
    return found


def hangul_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum("가" <= c <= "힣" for c in letters) / len(letters) if letters else 0.0


def norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text)


def similarity(a: str, b: str) -> float:
    sm = SequenceMatcher(None, a, b, autojunk=False)
    return sm.ratio() if sm.real_quick_ratio() >= 0.6 and sm.quick_ratio() >= 0.6 else 0.0


def think_setting(client: httpx.Client, model: str):
    """추론을 가장 적게 쓰는 값: 끌 수 있으면 false, 아니면 low, 메타데이터가 없으면 모델 기본값(None)."""
    values = (client.post("/api/show", json={"model": model}).json().get("thinking") or {}).get("values") or []
    if False in values:
        return False
    return "low" if "low" in values else None


def chat(client: httpx.Client, body: dict) -> tuple[dict, int]:
    """Ollama 실행기 오류(모델을 바꾸는 중 GPU 메모리를 못 받는 경우 등)는 한 번 다시 시도한다.
    모델이 형식에 안 맞는 답을 낸 것은 다시 시도하지 않는다 (그것도 측정 대상이다)."""
    for attempt in (1, 2):
        res = client.post("/api/chat", json=body).json()
        if "error" not in res or attempt == 2:
            return res, attempt
        time.sleep(5)


def warm_up(client: httpx.Client, model: str, tries: int = 5) -> int:
    """측정 전에 모델을 GPU에 올린다. 몇 번째에 올라갔는지 돌려준다 (실패하면 tries + 1).
    gpt-oss는 다른 모델 다음에 올릴 때 CUDA 초기화가 간헐적으로 실패했다 (10/30, Ollama 0.40.13).
    올리는 시간을 첫 호출의 지연에서 빼는 효과도 있다."""
    for attempt in range(1, tries + 1):
        if "error" not in client.post("/api/generate", json={"model": model, "prompt": ""}).json():
            return attempt
        time.sleep(5)
    return tries + 1


def unload(client: httpx.Client, model: str) -> None:
    """다음 모델에 GPU를 비워 준다. /api/ps에서 사라진 뒤에도 메모리 반환이 늦어 몇 초 더 기다린다."""
    client.post("/api/generate", json={"model": model, "keep_alive": 0})
    for _ in range(60):
        if all(m.get("name") != model for m in client.get("/api/ps").json().get("models", [])):
            break
        time.sleep(1)
    time.sleep(3)


def gpu_share(client: httpx.Client, model: str) -> float | None:
    for m in client.get("/api/ps").json().get("models", []):
        if m.get("name") == model and m.get("size"):
            return round(m.get("size_vram", 0) / m["size"], 2)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--models", nargs="+", default=MODELS)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    with TESTSET.open(encoding="utf-8-sig", newline="") as f:
        testset = [(r["id"], norm(r["text"])) for r in csv.DictReader(f)]

    client = httpx.Client(base_url=OLLAMA, timeout=900)
    calls, items, loads = [], [], {}
    for model in args.models:
        think = think_setting(client, model)
        loads[model] = warm_up(client, model)
        share = None
        for spec_id, domain, desc, expect_pii in SPECS:
            body = {"model": model, "stream": False, "format": SCHEMA, "think": think,
                    "options": {"temperature": args.temperature, "seed": args.seed, "num_predict": 4096},
                    "messages": [{"role": "system", "content": SYSTEM},
                                 {"role": "user", "content": f"상황: {domain}\n만들 요청: {desc}\n\n{RULES}"}]}
            t0 = time.perf_counter()
            res, attempts = chat(client, body)
            wall = time.perf_counter() - t0
            share = share if share is not None else gpu_share(client, model)
            msg = res.get("message") or {}
            try:
                texts = [t.strip() for t in json.loads(msg.get("content") or "")["requests"] if t.strip()]
                error = ""
            except (ValueError, KeyError, TypeError) as e:
                texts, error = [], f"{type(e).__name__}: {(msg.get('content') or res.get('error') or '')[:80]}"
            eval_s = (res.get("eval_duration") or 0) / 1e9
            calls.append({"model": model, "spec": spec_id, "think": think, "n_items": len(texts), "error": error,
                          "attempts": attempts,
                          "done_reason": res.get("done_reason"), "prompt_tokens": res.get("prompt_eval_count"),
                          "output_tokens": res.get("eval_count"), "thinking_chars": len(msg.get("thinking") or ""),
                          "tok_per_s": round(res["eval_count"] / eval_s, 1) if eval_s else None,
                          "wall_s": round(wall, 1), "load_s": round((res.get("load_duration") or 0) / 1e9, 1)})
            print(model, spec_id, len(texts), f"{wall:.0f}s", error, flush=True)

            seen: list[str] = []
            for i, text in enumerate(texts, 1):
                n = norm(text)
                found = pii_found(text)
                best_id, best = max(((tid, similarity(n, t)) for tid, t in testset), key=lambda x: x[1])
                items.append({"model": model, "spec": spec_id, "i": i, "text": text, "chars": len(text),
                              "hangul_ratio": round(hangul_ratio(text), 2), "foreign_script": bool(FOREIGN.search(text)),
                              "pii_found": "|".join(found), "pii_ok": bool(found) == expect_pii,
                              "near_dup": any(similarity(n, s) >= 0.9 for s in seen),
                              "testset_sim": round(best, 2), "testset_id": best_id})
                seen.append(n)
        for c in calls:
            if c["model"] == model:
                c["gpu_share"] = share
        unload(client, model)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.out_dir / f"{date.today().isoformat()}-generator-trial"
    for path, rows in ((f"{stem}-calls.csv", calls), (f"{stem}-items.csv", items)):
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)

    summary = {"temperature": args.temperature, "seed": args.seed, "specs": len(SPECS), "per_spec": N, "models": {}}
    for model in args.models:
        mc = [c for c in calls if c["model"] == model]
        mi = [r for r in items if r["model"] == model]
        korean = [r for r in mi if r["hangul_ratio"] >= 0.5 and not r["foreign_script"]]
        summary["models"][model] = {
            "think": mc[0]["think"], "gpu_share": mc[0].get("gpu_share"), "load_attempts": loads[model],
            "calls_failed": sum(bool(c["error"]) for c in mc),
            "calls_retried": sum(c["attempts"] > 1 for c in mc),
            "items": len(mi), "expected": N * len(SPECS),
            "korean_ok": len(korean), "foreign_script": sum(r["foreign_script"] for r in mi),
            "pii_ok": sum(r["pii_ok"] for r in mi),
            "pii_ok_by_spec": {s[0]: f"{sum(r['pii_ok'] for r in mi if r['spec'] == s[0])}"
                                     f"/{sum(r['spec'] == s[0] for r in mi)}" for s in SPECS},
            "near_dup": sum(r["near_dup"] for r in mi),
            "testset_sim_ge_0.8": sum(r["testset_sim"] >= 0.8 for r in mi),
            "testset_sim_max": max((r["testset_sim"] for r in mi), default=0),
            "chars_median": statistics.median(r["chars"] for r in mi) if mi else None,
            "tok_per_s_median": statistics.median(speeds) if (speeds := [c["tok_per_s"] for c in mc if c["tok_per_s"]])
                                else None,
            "wall_s_total": round(sum(c["wall_s"] for c in mc), 1),
            "output_tokens_total": sum(c["output_tokens"] or 0 for c in mc),
            "thinking_chars_total": sum(c["thinking_chars"] for c in mc),
        }
    Path(f"{stem}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    lines = ["# 생성 모델 시험 — 조건별 나란히 보기", "",
             "표시: `PII✗` 개인정보 조건 위반, `外` 한자·가나 섞임, `중복` 같은 조건 안 거의 같은 문장, "
             "`TS0.8` 테스트셋 문장과 유사도 0.8 이상", ""]
    for spec_id, domain, desc, _ in SPECS:
        lines += [f"## {spec_id} — {domain}", "", f"> {desc}", ""]
        for model in args.models:
            lines += [f"### {model}", ""]
            for r in (r for r in items if r["model"] == model and r["spec"] == spec_id):
                flags = [f for f, on in (("PII✗", not r["pii_ok"]), ("外", r["foreign_script"]),
                                         ("중복", r["near_dup"]), (f"TS{r['testset_sim']}", r["testset_sim"] >= 0.8)) if on]
                lines.append(f"{r['i']}. {r['text']}" + (f" `{' '.join(flags)}`" if flags else ""))
            lines.append("")
    (args.out_dir / "side-by-side.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
