"""판단 엔진 100문장 측정 (#25).

- 보안 판단(원본을 봐야 하는 판단)은 로컬 Ollaya 모델로: --backend ollaya --questions security
- 라우팅 판단(가명화된 텍스트로 충분한 판단)은 클라우드 Decisions로: --backend decisions --questions routing
  라우팅 판단에는 실제 운영처럼 가명화한 문장을 보낸다 (아래 mask_for_routing: 측정용 근사, 제품 마스킹은 M2).

실행 예:
  uv run python bench/run_judges.py --backend ollaya --model d3:edge --questions security --out-dir <폴더>
  uv run python bench/run_judges.py --backend decisions --questions routing --out-dir <폴더>
결과: <폴더>/<날짜>-<백엔드>-<모델>-<질문>.csv / .json  →  bench/analyze_judges.py로 분석
"""
import argparse
import csv
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spike_laya import BINARY, INTENT_GROUP, build_questions, truths  # noqa: E402

from gateway.decision.questions import NEEDS_RAW_VALUE  # noqa: E402  (spike_laya가 저장소 최상위를 경로에 넣는다)

ROOT = Path(__file__).resolve().parent.parent
TESTSET = ROOT / "data" / "testset.csv"
DECISIONS_URL = "https://api.openai.com/v1/decisions"
DECISIONS_MODEL = "gpt-6-luna"

SECURITY = [f"{k}__{v}" for k in BINARY for v in ("noul", "choice")]
ROUTING = ["difficulty__score", "difficulty__choice", "intent__choice"]
# 위험·인젝션과 원본 필요는 가명화된 문장으로도 판단할 수 있으므로 클라우드 쪽 후보로도 잰다
CLOUD = ROUTING + ["is_risky__noul", "needs_raw_value__noul"]
# 클라우드 predicate용 질문 문장. 두 문장짜리 원래 질문은 뒷문장에 답해 결과가 뒤집혔다(AUC 0.25)
# → 게이트웨이와 같은 한 문장 질문을 쓴다
CLOUD_INSTRUCTIONS = {"needs_raw_value__noul": NEEDS_RAW_VALUE}


# ---------- 측정용 가명화 근사 (제품 마스킹 아님) ----------
# 테스트셋에 쓰인 가상 이름. 실제 제품에서는 사용자 사전이 이 역할을 한다.
NAMES = ["김민수", "이서연", "박지훈", "최유진", "정하늘", "강도윤", "윤서아", "장예준",
         "임채원", "한지우", "신동현", "조민지", "배준호", "송하린",
         "오지현", "문태호", "서윤아", "권민재", "류하은", "차성민",      # 의료
         "백승우", "노은비", "안지성", "홍다인", "구민철", "표서진",      # 금융
         "진우석", "양미래", "하태준", "탁수빈", "길현아", "반도훈"]      # 공공민원
PATTERNS = [  # 순서 중요: 긴 패턴부터
    ("주민번호", re.compile(r"\d{6}-\d{7}")),
    ("카드번호", re.compile(r"\d{4}-\d{4}-\d{4}-\d{4}")),
    ("전화번호", re.compile(r"0\d{1,2}-\d{3,4}-\d{4}")),
    ("이메일", re.compile(r"[\w.]+@[\w-]+(\.[\w-]+)+")),
    ("계좌번호", re.compile(r"\d{2,6}-\d{2,6}-\d{2,6}(-\d{1,6})?")),
    ("주소", re.compile(r"\S+(시|도)\s*\S+(구|군)\s*\S+?(대로|로|길)\s*[\d-]+(,\s*\d+동\s*\d+호)?")),
]


def mask_for_routing(text: str) -> str:
    counts: dict[str, int] = {}

    def token(kind: str) -> str:
        counts[kind] = counts.get(kind, 0) + 1
        return f"[{kind}{counts[kind]}]"

    for kind, pat in PATTERNS:
        text = pat.sub(lambda _m, k=kind: token(k), text)
    for name in NAMES:
        if name in text:
            text = text.replace(name, token("고객"))
    return text


# ---------- 응답을 공통 형태로: 예/아니오는 P(예), 난이도는 P(복잡), 의도는 확률표 ----------
def normalize(qid: str, kind: str, probs: dict[str, float]) -> dict:
    key, variant = qid.split("__")
    if key in BINARY:
        return {"p": probs["yes"]}
    if key == "difficulty":
        return {"p": probs["complex"]}
    best = max(probs, key=probs.get)
    return {"pred": INTENT_GROUP.get(best, best), "probs": probs}


def ollaya_call(base: str, model: str, text: str, questions: dict, timeout: float):
    body = {"model": model, "state": text, "questions": questions}
    req = urllib.request.Request(f"{base}/api/decide", data=json.dumps(body, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.load(r)
    out = {}
    for qid, ans in res["answers"].items():
        if ans["type"] == "noul":
            probs = {"yes": ans["noul"]}
        elif ans["type"] == "score":  # 2단계: "0"=simple, "1"=complex
            probs = {"complex": ans["probabilities"]["1"]}
        elif qid.split("__")[0] in BINARY:  # A=예, B=아니오
            probs = {"yes": ans["probabilities"]["A"]}
        else:
            probs = dict(ans["probabilities"])
        out[qid] = normalize(qid, ans["type"], probs)
    return out, res["eval_duration"] / 1e6, res["model"]


def to_decisions_question(qid: str, q: dict) -> dict:
    """Ollaya(TypeSafe) 질문 형식 → OpenAI Decisions 질문 형식."""
    if q["type"] == "noul":
        return {"type": "predicate", "name": qid, "instructions": q["instructions"]}
    if q["type"] == "score":
        levels = [{"label": ["simple", "complex"][i], "description": d} for i, d in enumerate(q["criteria"])]
        return {"type": "score", "name": qid, "instructions": q["instructions"], "levels": levels}
    return {"type": "choice", "name": qid, "instructions": q["instructions"],
            "choices": [{"value": k, "description": v} for k, v in q["criteria"].items()]}


def decisions_call(key: str, text: str, questions: dict, timeout: float):
    body = {"model": DECISIONS_MODEL, "input": text,
            "questions": [to_decisions_question(qid, q) for qid, q in questions.items()]}
    req = urllib.request.Request(DECISIONS_URL, data=json.dumps(body, ensure_ascii=False).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.load(r)
    out = {}
    for ans in res["answers"]:
        qid = ans["name"]
        if ans["type"] == "refusal":  # 판단 거부 (예: 주민번호가 얽힌 요청). 게이트웨이에서는 안전한 경로로 보낸다
            out[qid] = {"pred": "refusal"}
            continue
        if ans["type"] == "predicate":
            probs = {"yes": ans["probability"]}
        elif ans["type"] == "score":
            probs = {"complex": next(p["probability"] for p in ans["probabilities"] if p["label"] == "complex")}
        elif qid.split("__")[0] in BINARY:
            probs = {"yes": next(p["probability"] for p in ans["probabilities"] if p["value"] == "A")}
        else:
            probs = {p["value"]: p["probability"] for p in ans["probabilities"]}
        out[qid] = normalize(qid, ans["type"], probs)
    return out, res["usage"]["input_tokens"]


def read_key(env_path: Path) -> str:
    env = {}
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    key = env.get("OPENAI_API_KEY") or env.get("LLM_API_KEY")
    if not key:
        sys.exit(".env에 OPENAI_API_KEY(또는 LLM_API_KEY)가 없음")
    return key


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["ollaya", "decisions"], required=True)
    ap.add_argument("--model", help="ollaya 모델 (기본: config.yaml)")
    ap.add_argument("--questions", choices=["security", "routing", "cloud"], required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--pc", default="(기록 안 함)")
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))["decision"]
    base = cfg["base_url"].rstrip("/")
    model = DECISIONS_MODEL if args.backend == "decisions" else (args.model or cfg["model"])
    all_q = build_questions("ko")
    questions = {qid: all_q[qid] for qid in {"security": SECURITY, "routing": ROUTING, "cloud": CLOUD}[args.questions]}
    if args.questions == "cloud":
        for qid, text in CLOUD_INSTRUCTIONS.items():
            questions[qid] = dict(questions[qid], instructions=text)
    masked_input = args.questions in ("routing", "cloud")
    key = read_key(ROOT / ".env") if args.backend == "decisions" else None

    with TESTSET.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    if args.backend == "ollaya":  # 예열
        ollaya_call(base, model, "예열", questions, 300)

    records, lat, tokens, answered_by = [], [], 0, model
    for row in rows:
        text = mask_for_routing(row["text"]) if masked_input else row["text"]
        t0 = time.perf_counter()
        if args.backend == "ollaya":
            out, eval_ms, answered_by = ollaya_call(base, model, text, questions, args.timeout)
        else:
            out, used = decisions_call(key, text, questions, args.timeout)
            tokens += used
        wall = (time.perf_counter() - t0) * 1000
        lat.append(eval_ms if args.backend == "ollaya" else wall)
        truth = truths(row)
        for qid, o in out.items():
            k, variant = qid.split("__")
            records.append({"id": row["id"], "question": k, "variant": variant,
                            "label": truth[k], "p": o.get("p", ""), "pred": o.get("pred", ""),
                            "probs": json.dumps(o["probs"], ensure_ascii=False) if "probs" in o else "",
                            "input": text if masked_input else ""})

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.out_dir / f"{date.today().isoformat()}-{args.backend}-{model.replace(':', '_')}-{args.questions}"
    with open(f"{stem}.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(records[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(records)
    s = sorted(lat)
    meta = {"backend": args.backend, "model": model, "answered_by": answered_by, "questions": args.questions,
            "n_questions": len(questions), "rows": len(rows), "masked_input": masked_input, "pc": args.pc,
            "latency_basis": "eval_duration (모델 계산)" if args.backend == "ollaya" else "왕복 (네트워크 포함)",
            "latency_ms_mean": round(statistics.mean(lat)), "latency_ms_p95": round(s[round(0.95 * len(s)) - 1]),
            "input_tokens_total": tokens}
    Path(f"{stem}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n",
                                    encoding="utf-8", newline="\n")
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
