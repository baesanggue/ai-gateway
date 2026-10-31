"""학습 데이터 생성 (#21): gemma4:12b로 한국어 요청 문장을 만들고, 범주로 정답을 붙여 Laya 학습 형식(JSONL)으로 저장한다.

흐름
1. 조건(범주 × 업종 × 말투)마다 gemma4에게 10문장을 만들게 한다. 개인정보 자리는 {이름}·{전화번호} 같은 자리표시로 쓰게 한다
2. 코드가 자리표시에 가상 값을 무작위로 채운다. 반복 숫자가 없고, 주민번호 뒷자리 첫 숫자는 생년과 맞고, 카드번호는 Luhn 검사를 통과한다
3. 개인정보가 있는 문장은 가명화본([고객1]·[전화번호1])도 만든다. 게이트웨이가 클라우드 판단에 보내는 모양과 같다
4. 자동 검사로 거른다: 형식, 한국어, 자리표시 조건, 자리표시 없이 직접 쓴 이름, 중복, 테스트셋과의 유사도
5. 범주가 확실히 정하는 정답만 붙인다. 정하지 못하는 질문은 비워 둔다 (laya는 정답이 없는 질문을 학습에서 뺀다)

- 형식: laya 0.4.17 `laya.train.finetune`의 `{state, questions, expected}` 줄 (finetune/.venv의 laya/train.py에서 확인)
- 질문은 gateway의 질문 정의를 systemone_questions로 바꿔 넣어, 추론 때와 글자 하나까지 같게 한다
- 테스트셋 문장은 프롬프트에 넣지 않고, 테스트셋 이름은 가상 이름 후보에서 뺀다. OpenAI·xAI 출력은 쓰지 않는다 (D15)

실행: uv run python finetune/generate.py --per-category 20 --out-dir finetune/data/pilot-<날짜>
전제: Ollama가 127.0.0.1:11434에서 돌고 gemma4:12b를 받아 둔 상태
"""
import argparse
import csv
import json
import random
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
from generator_trial import FOREIGN, chat, hangul_ratio, norm, similarity, warm_up  # noqa: E402
from run_judges import NAMES as TESTSET_NAMES  # noqa: E402

from gateway.decision.engines import systemone_questions  # noqa: E402
from gateway.decision.questions import QUESTIONS  # noqa: E402

TESTSET = ROOT / "data" / "testset.csv"
OLLAMA = "http://127.0.0.1:11434"
MODEL = "gemma4:12b"
N = 10   # 호출 한 번에 만들 문장 수

INDUSTRIES = {
    "shop": "온라인 쇼핑몰 — 고객이 고객센터 챗봇에 입력하거나, 상담원이 업무용 AI에 입력",
    "office": "일반 회사 사내 업무 — 직원이 사내 업무용 AI에 입력",
    "medical": "병원 — 환자가 병원 챗봇에 입력하거나, 원무과 직원이 업무용 AI에 입력",
    "finance": "은행·카드사 — 고객이 금융 상담 챗봇에 입력하거나, 상담 직원이 업무용 AI에 입력",
    "public": "구청·주민센터 — 주민이 민원 챗봇에 입력하거나, 민원 담당 공무원이 업무용 AI에 입력",
}
STYLES = ["해요체 존댓말", "합니다체 격식 존댓말", "반말",
          "띄어쓰기를 일부 틀리거나 오타를 한두 개 섞은 편한 말투", "존댓말과 반말을 섞어서"]

PII_TOKEN = {"이름": "고객", "전화번호": "전화번호", "이메일": "이메일", "주민번호": "주민번호",
             "계좌번호": "계좌번호", "카드번호": "카드번호", "주소": "주소"}   # 자리표시 → 가명 토큰 종류
PLACEHOLDER = re.compile(r"\{(" + "|".join([*PII_TOKEN, "주문번호"]) + r")(\d*)\}")
NO_PII = "사람 이름, 전화번호, 이메일, 주민등록번호, 계좌번호, 카드번호, 주소는 넣지 않는다."


@dataclass(frozen=True)
class Category:
    id: str
    industries: tuple[str, ...]
    desc: str
    # 정답 표: 이 범주가 확실히 정하는 정답만. has_pii는 범주가 아니라 실제로 채운 자리표시로 정한다
    labels: dict[str, object]
    allowed: frozenset[str] = frozenset()     # 쓸 수 있는 자리표시
    required: frozenset[str] = frozenset()    # 이 중 하나 이상 있어야 한다
    rule: str = NO_PII                         # 자리표시 규칙 (프롬프트)


def _pii_rule(kinds: tuple[str, ...]) -> str:
    marks = ", ".join("{" + k + "}" for k in kinds)
    return (f"개인정보가 들어갈 자리에는 실제 값 대신 자리표시를 그대로 쓴다. 쓸 수 있는 자리표시: {marks}. "
            "같은 종류가 한 문장에 두 번 필요하면 {이름2}처럼 번호를 붙인다. 자리표시가 아닌 곳에 사람 이름이나 번호를 직접 쓰지 않는다.")


ALL = tuple(INDUSTRIES)
CUSTOMER = ("shop", "medical", "finance", "public")
F, T = False, True
# difficulty: 0 = 단순, 1 = 복잡 (score 레벨 번호). intent: questions.INTENTS의 라벨
CATEGORIES = [
    Category("faq", CUSTOMER,
             "고객·주민이 묻는 짧은 자주 묻는 질문. 운영 시간, 신청·예약 방법, 준비물·서류, 배송·반품·결제 절차처럼 회사가 정해 둔 답이 있는 것.",
             {"is_risky": F, "is_confidential": F, "difficulty": 0, "intent": "faq"}),
    Category("service", CUSTOMER,
             "상품이나 서비스의 사용법·관리법·추천·자격을 묻는 이용 문의. 몇 문장으로 짧게 답할 수 있는 것.",
             {"is_risky": F, "is_confidential": F, "difficulty": 0, "intent": "service"}),
    Category("task_simple", ALL,
             "직원이 AI에게 맡기는 간단한 업무. 짧은 문장 다듬기, 짧은 공지·안내문 작성, 단어 번역, 한두 줄 요약처럼 금방 끝나는 일.",
             {"is_risky": F, "is_confidential": F, "difficulty": 0, "intent": "task"}),
    Category("task_complex", ALL,
             "직원이 AI에게 맡기는 복잡한 업무. 여러 조건이나 자료를 비교·분석하거나, 계획을 세우거나, 긴 문서를 써야 하는 일. 조건을 2개 이상 구체적으로 적는다.",
             {"is_risky": F, "is_confidential": F, "difficulty": 1, "intent": "task"}),
    Category("task_short_complex", ("office", "shop", "finance"),   # 경계: 짧지만 복잡
             "한 문장으로 짧지만 실제로는 여러 단계의 분석이나 긴 문서가 필요한 업무 요청. 25자 안팎으로 짧게 쓴다.",
             {"is_risky": F, "is_confidential": F, "difficulty": 1, "intent": "task"}),
    Category("pii_maskable", CUSTOMER,
             "직원이 고객의 개인정보를 넣어 맡기는 간단한 업무(안내 문자·메일 작성, 접수 확인 문구 등). 개인정보 값을 가려도 AI가 문장을 쓸 수 있는 일.",
             {"needs_raw_value": F, "is_risky": F, "is_confidential": F, "difficulty": 0, "intent": "task"},
             allowed=frozenset({"이름", "전화번호", "이메일", "주소", "계좌번호"}), required=frozenset(PII_TOKEN),
             rule=_pii_rule(("이름", "전화번호", "이메일", "주소", "계좌번호"))),
    Category("needs_raw", CUSTOMER,
             "개인정보의 실제 값을 봐야만 답할 수 있는 요청. 주민번호로 나이·성별 계산, 번호의 자릿수·형식 확인, 두 번호가 같은지 비교, "
             "주소를 영문으로 바꾸기처럼 값 자체를 계산하거나 바꾸는 일. 회사 시스템을 조회해야 하는 요청(한도, 잔액, 결제 내역)은 만들지 않는다.",
             {"needs_raw_value": T, "is_risky": F, "is_confidential": F},
             allowed=frozenset(PII_TOKEN),
             required=frozenset({"주민번호", "계좌번호", "카드번호", "전화번호", "주소", "이메일"}),
             rule=_pii_rule(("이름", "주민번호", "계좌번호", "카드번호", "전화번호", "주소", "이메일"))),
    Category("pii_word_only", CUSTOMER,   # 경계: 개인정보 '단어'만
             "주민등록번호·전화번호·계좌번호·주소 같은 개인정보 '단어'는 나오지만 실제 값은 없는 문의(발급·변경·확인 방법 등).",
             {"is_risky": F, "is_confidential": F, "difficulty": 0}),
    Category("order_number", CUSTOMER,    # 경계: 개인정보가 아닌 번호
             "주문번호·운송장 번호·예약번호·접수번호 같은 개인정보가 아닌 번호가 들어간 짧은 문의.",
             {"is_risky": F, "is_confidential": F, "difficulty": 0},
             allowed=frozenset({"주문번호"}), required=frozenset({"주문번호"}),
             rule="번호 자리는 {주문번호}로 쓴다(주문번호·운송장 번호·예약번호·접수번호 모두). " + NO_PII),
    Category("confidential", ("office", "shop", "medical", "finance"),
             "외부에 알려지면 안 되는 회사 기밀의 구체적인 내용(미공개 실적 수치, 인수합병 대상과 금액, 인사평가·연봉, 거래처 단가·계약 조건, "
             "출시 전 제품 정보)이 문장에 직접 들어 있는 업무 요청. 기밀의 주제만 말하고 내용이 없는 요청은 만들지 않는다.",
             {"is_confidential": T, "is_risky": F, "intent": "task"},
             allowed=frozenset({"이름"}),
             rule="사람 이름이 필요하면 {이름}으로 쓴다. 전화번호·이메일 같은 연락처는 넣지 않는다."),
    Category("risky", ALL,
             "AI가 받은 지시를 무시하게 하는 프롬프트 인젝션, 시스템 프롬프트나 다른 사용자 정보를 빼내려는 요청, 해킹·사기·불법 행위를 시키는 요청. "
             "해를 끼칠 구체적인 방법은 쓰지 말고 요청 문장만 만든다.",
             {"is_risky": T, "is_confidential": F}),
    Category("security_benign", ("office", "finance", "public"),   # 경계: 보안 주제지만 정상 업무
             "보안·해킹·개인정보·사기 같은 단어가 나오지만 정당한 업무 요청(보안 교육 자료, 피싱 주의 안내문, 개인정보 처리방침 검토, 사기 예방 공지 등).",
             {"is_risky": F, "is_confidential": F, "intent": "task"}),
    Category("off_topic", ALL,
             "회사 업무와 전혀 관계없는 개인적인 질문이나 잡담(날씨, 연애, 숙제, 운세, 스포츠, 맛집 등).",
             {"is_risky": F, "is_confidential": F, "intent": "off_topic"}),
]
DIFFICULTY_LEVELS = list(QUESTIONS["difficulty"].criteria)   # ["simple", "complex"]

SYSTEM = ("너는 한국어 업무용 AI 챗봇에 들어오는 요청을 분류하는 모델의 학습 데이터를 만든다. "
          "실제 사람이 챗봇 입력창에 칠 법한 자연스러운 한국어 요청 문장을 만든다.")
SCHEMA = {"type": "object", "required": ["requests"],
          "properties": {"requests": {"type": "array", "items": {"type": "string"}}}}

# 자리표시 없이 직접 쓴 이름 + 호칭 (예: "김철수 고객", "박지은님"). 개인정보 정답이 틀어지므로 버린다
SURNAMES = "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허남심노하곽성차주우구민진나지엄채원천방공현함변염여추도소석선설마길연위표"
DIRECT_NAME = re.compile(rf"(?<![가-힣])([{SURNAMES}][가-힣]{{2}})\s?(님|씨|고객|환자|과장|대리|부장|팀장|주임|사원|선생님)")
# "구매한 고객", "신청된 환자"처럼 꾸밈말로 끝나는 세 글자는 이름이 아니다 (10/31 시범에서 오탐)
NOT_NAME_ENDINGS = set("한은는던할된인을를의들적")


def has_direct_name(text: str) -> bool:
    return any(m.group(1)[-1] not in NOT_NAME_ENDINGS for m in DIRECT_NAME.finditer(text))


# ── 가상 값 ───────────────────────────────────────────────

# 흔한 이름. 음절을 무작위로 조합하면 "동태", "도주"처럼 이름 같지 않은 이름이 나왔다 (10/31 시범)
GIVEN = ("민준 서준 도윤 예준 시우 하준 지호 주원 지후 준우 현우 지훈 성민 정훈 상훈 동현 승현 재원 태윤 준호 "
         "민재 현준 우진 건우 영수 성훈 경민 정민 서연 서윤 지우 서현 민서 하은 하윤 윤서 지유 지민 채원 수아 "
         "지아 지윤 은서 다은 예은 수빈 유진 소윤 가은 나연 다현 은지 혜원 은정 혜진 지영 수진 민정 미영").split()
CITIES = ["서울시", "부산시", "대구시", "인천시", "광주시", "대전시", "울산시", "경기도 수원시", "경기도 성남시", "충청북도 청주시"]
GU = ["중구", "동구", "서구", "남구", "북구", "새솔구", "한빛구", "누리구"]
STREETS = ["가상로", "예시길", "샘플대로", "연습로", "모의길", "시범로", "본보기길"]
EMAIL_USERS = ["minsu", "seoyeon", "jihoon", "yujin", "haneul", "doyun", "seoa", "yejun", "chaewon", "jiwoo", "dohyun", "hayoon"]
EMAIL_DOMAINS = ["example.com", "example.co.kr", "example.org", "example.net"]   # 예시용으로 예약된 도메인만


class Faker:
    """자리표시를 채울 가상 값. 테스트셋 이름은 쓰지 않는다."""

    def __init__(self, rng: random.Random):
        self.r = rng

    def digits(self, n: int) -> str:
        while True:
            s = "".join(self.r.choice("0123456789") for _ in range(n))
            # 5555·0000 같은 반복 숫자를 피한다. 2자리 이하는 숫자 3종류가 나올 수 없어 이 조건을 빼야 멈춘다
            if n < 3 or len(set(s)) > 2:
                return s

    def 이름(self) -> str:
        while True:
            name = self.r.choice(SURNAMES[:30]) + self.r.choice(GIVEN)
            if name not in TESTSET_NAMES:
                return name

    def 전화번호(self) -> str:
        return f"010-{self.r.choice('23456789')}{self.digits(3)}-{self.digits(4)}"

    def 이메일(self) -> str:
        return f"{self.r.choice(EMAIL_USERS)}{self.r.randint(1, 99)}@{self.r.choice(EMAIL_DOMAINS)}"

    def 주민번호(self) -> str:
        year = self.r.randint(1950, 2006)
        month, day = self.r.randint(1, 12), self.r.randint(1, 28)
        gender = self.r.choice("12" if year < 2000 else "34")   # 뒷자리 첫 숫자는 생년·성별과 맞춘다
        return f"{year % 100:02d}{month:02d}{day:02d}-{gender}{self.digits(6)}"

    def 계좌번호(self) -> str:
        return "-".join(self.digits(n) for n in self.r.choice([(3, 2, 6), (3, 3, 6), (6, 2, 6), (4, 3, 6), (3, 6, 5)]))

    def 카드번호(self) -> str:
        body = [int(c) for c in self.r.choice("45") + self.digits(14)]
        total = sum(d if i % 2 else (d * 2 - 9 if d * 2 > 9 else d * 2) for i, d in enumerate(body))
        num = "".join(map(str, body)) + str((10 - total % 10) % 10)   # Luhn 검사 숫자
        return "-".join(num[i:i + 4] for i in range(0, 16, 4))

    def 주소(self) -> str:
        base = f"{self.r.choice(CITIES)} {self.r.choice(GU)} {self.r.choice(STREETS)} {self.r.randint(1, 300)}"
        return base + (f", {self.r.randint(101, 115)}동 {self.r.randint(1, 25)}0{self.r.randint(1, 4)}호" if self.r.random() < 0.4 else "")

    def 주문번호(self) -> str:   # 개인정보가 아닌 번호. 일부는 계좌번호처럼 보이는 모양 (경계 사례)
        fmt = self.r.choice(["order", "order_hyphen", "waybill", "receipt", "reservation"])
        return {"order": lambda: f"2026{self.r.randint(1, 12):02d}{self.r.randint(1, 28):02d}{self.digits(5)}",
                "order_hyphen": lambda: f"2026{self.r.randint(1, 12):02d}{self.r.randint(1, 28):02d}-{self.digits(6)}",
                "waybill": lambda: self.digits(12),
                "receipt": lambda: f"A-2026-{self.digits(5)}",
                "reservation": lambda: f"R{self.digits(7)}"}[fmt]()


@dataclass
class Filled:
    original: str
    masked: str
    kinds: list[str] = field(default_factory=list)   # 채운 개인정보 종류 (중복 포함)


def fill(template: str, faker: Faker) -> Filled:
    """자리표시를 가상 값으로 채운 원문과, 같은 자리를 가명 토큰으로 바꾼 가명화본을 함께 만든다.
    같은 자리표시({이름}이 두 번)는 같은 값·같은 토큰이 된다 (가명화의 "같은 값 = 같은 토큰" 규칙)."""
    values, tokens, counts, kinds = {}, {}, Counter(), []

    def sub(m, masked):
        key, kind = m.group(0), m.group(1)
        if key not in values:
            values[key] = getattr(faker, kind)()
            if kind in PII_TOKEN:
                counts[kind] += 1
                tokens[key] = f"[{PII_TOKEN[kind]}{counts[kind]}]"
                kinds.append(kind)
        return tokens.get(key, values[key]) if masked else values[key]

    original = PLACEHOLDER.sub(lambda m: sub(m, False), template)
    masked = PLACEHOLDER.sub(lambda m: sub(m, True), template)
    return Filled(original, masked, kinds)


def check(template: str, cat: Category) -> str | None:
    """버릴 이유. 통과하면 None."""
    used = {m.group(1) for m in PLACEHOLDER.finditer(template)}
    if re.search(r"[{}]", PLACEHOLDER.sub("", template)):
        return "unknown_placeholder"
    if used - cat.allowed:
        return "placeholder_not_allowed"
    if cat.required and not used & cat.required:
        return "placeholder_missing"
    if has_direct_name(PLACEHOLDER.sub("", template)):
        return "direct_name"
    text = PLACEHOLDER.sub("", template)
    if FOREIGN.search(text) or hangul_ratio(text) < 0.5:
        return "not_korean"
    return None


def labels_for(cat: Category, has_pii: bool) -> dict[str, object]:
    out = {"has_pii": has_pii, **cat.labels}
    if "difficulty" in out:
        out["difficulty"] = DIFFICULTY_LEVELS[out["difficulty"]]
    return out


def row(state: str, labels: dict[str, object]) -> dict:
    """laya `{state, questions, expected}` 한 줄. difficulty는 score라 레벨 번호로 넣는다."""
    expected = {q: (DIFFICULTY_LEVELS.index(v) if q == "difficulty" else v) for q, v in labels.items()}
    return {"state": state, "questions": systemone_questions({q: QUESTIONS[q] for q in labels}), "expected": expected}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--per-category", type=int, default=20, help="범주마다 만들 문장 수 (10의 배수, 거르기 전)")
    ap.add_argument("--temperature", type=float, default=0.9)
    ap.add_argument("--seed", type=int, default=1031)
    ap.add_argument("--review", type=int, default=50, help="직접 검토할 무작위 표본 수")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    faker = Faker(rng)
    with TESTSET.open(encoding="utf-8-sig", newline="") as f:
        testset = [(r["id"], norm(r["text"])) for r in csv.DictReader(f)]

    client = httpx.Client(base_url=OLLAMA, timeout=900)
    print("warm-up attempts:", warm_up(client, MODEL), flush=True)
    items, call_no, t0 = [], 0, time.perf_counter()
    for cat in CATEGORIES:
        seen: list[str] = []
        for _ in range(args.per_category // N):
            industry, style = rng.choice(cat.industries), rng.choice(STYLES)
            prompt = (f"업종: {INDUSTRIES[industry]}\n만들 요청: {cat.desc}\n말투: {style}\n\n규칙:\n"
                      f"- 정확히 {N}개를 만든다. 서로 상황이 겹치지 않게 한다.\n"
                      "- 요청 문장만 쓴다. 번호, 따옴표, 설명, 라벨을 붙이지 않는다.\n"
                      f"- {cat.rule}\n- 실제 회사 이름과 실존 인물 이름은 쓰지 않는다.\n"
                      f'- 답은 {{"requests": ["요청1", ..., "요청{N}"]}} 형식의 JSON 하나만 출력한다.')
            call_no += 1
            res, _ = chat(client, {"model": MODEL, "stream": False, "format": SCHEMA, "think": False,
                                   "options": {"temperature": args.temperature, "seed": args.seed + call_no},
                                   "messages": [{"role": "system", "content": SYSTEM},
                                                {"role": "user", "content": prompt}]})
            try:
                templates = [t.strip() for t in json.loads(res["message"]["content"])["requests"] if t.strip()]
            except (KeyError, TypeError, ValueError):
                templates = []
                print(cat.id, "응답 형식 오류", str(res.get("error", ""))[:80], flush=True)
            for template in templates:
                drop = check(template, cat)
                filled = fill(template, faker) if drop is None else Filled(template, template)
                n = norm(filled.original)
                if drop is None and any(similarity(n, s) >= 0.9 for s in seen):
                    drop = "near_duplicate"
                ts_id, ts_sim = max(((tid, similarity(n, t)) for tid, t in testset), key=lambda x: x[1])
                if drop is None and ts_sim >= 0.8:
                    drop = "testset_overlap"
                if drop is None:
                    seen.append(n)
                items.append({"category": cat.id, "industry": industry, "style": style, "template": template,
                              "original": filled.original, "masked": filled.masked if filled.kinds else "",
                              "pii_kinds": "|".join(filled.kinds), "testset_sim": round(ts_sim, 2),
                              "testset_id": ts_id, "drop": drop or ""})
        kept = sum(1 for i in items if i["category"] == cat.id and not i["drop"])
        print(f"{cat.id}: {kept} kept", flush=True)

    # laya 학습 줄: 원문 한 줄 + (개인정보가 있으면) 가명화본 한 줄
    # 가명화본의 has_pii는 False다. 토큰만 남은 문장에 '남은 개인정보 없음'이라고 답하도록 가르친다
    # (엄격 수준은 가명화 뒤 남은 개인정보 의심을 차단에 쓴다, docs/routing.md)
    lines = []
    for it in (i for i in items if not i["drop"]):
        cat = next(c for c in CATEGORIES if c.id == it["category"])
        lines.append({"source": "original", **it, "labels": labels_for(cat, bool(it["pii_kinds"]))})
        if it["masked"]:
            lines.append({"source": "masked", **it, "labels": labels_for(cat, False)})

    args.out_dir.mkdir(parents=True, exist_ok=True)
    with open(args.out_dir / "train.jsonl", "w", encoding="utf-8") as f:
        for ln in lines:
            state = ln["masked"] if ln["source"] == "masked" else ln["original"]
            f.write(json.dumps(row(state, ln["labels"]), ensure_ascii=False) + "\n")
    with open(args.out_dir / "items.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(items[0]))
        w.writeheader()
        w.writerows(items)

    sample = random.Random(args.seed).sample(lines, min(args.review, len(lines)))
    with open(args.out_dir / "review.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["no", "category", "source", "text", *QUESTIONS, "검토(O/X)", "메모"])
        for k, ln in enumerate(sample, 1):
            state = ln["masked"] if ln["source"] == "masked" else ln["original"]
            w.writerow([k, ln["category"], ln["source"], state,
                        *[_show(ln["labels"].get(q)) for q in QUESTIONS], "", ""])

    md = ["# 학습 데이터 표본 검토", "",
          "정답이 틀린 줄의 번호를 적어 주세요. 비어 있는 판단은 이 범주로는 정답을 정할 수 없어 학습에서 빠지는 질문입니다.", "",
          "| 번호 | 범주 | 문장 | 정답 |", "|---|---|---|---|"]
    for k, ln in enumerate(sample, 1):
        state = ln["masked"] if ln["source"] == "masked" else ln["original"]
        answer = " · ".join(f"{LABEL_KO[q]} {_show(v, q)}" for q, v in ln["labels"].items())
        md.append(f"| {k} | {ln['category']}{' (가명화)' if ln['source'] == 'masked' else ''} | {state} | {answer} |")
    (args.out_dir / "review.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    summary = {"model": MODEL, "temperature": args.temperature, "seed": args.seed, "calls": call_no,
               "seconds": round(time.perf_counter() - t0, 1),
               "generated": len(items), "kept": sum(not i["drop"] for i in items),
               "dropped": dict(Counter(i["drop"] for i in items if i["drop"])),
               "lines": len(lines), "masked_lines": sum(ln["source"] == "masked" for ln in lines),
               "kept_by_category": dict(Counter(i["category"] for i in items if not i["drop"])),
               "label_counts": {q: dict(Counter(_show(ln["labels"][q]) for ln in lines if q in ln["labels"]))
                                for q in QUESTIONS}}
    (args.out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


LABEL_KO = {"has_pii": "개인정보", "is_confidential": "기밀", "needs_raw_value": "원본 필요", "is_risky": "위험",
            "difficulty": "난이도", "intent": "의도"}
VALUE_KO = {"simple": "단순", "complex": "복잡", "faq": "FAQ", "service": "이용 문의", "task": "업무", "off_topic": "무관"}


def _show(v, q: str | None = None) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "예" if v else "아니오"
    return VALUE_KO.get(v, str(v)) if q else str(v)


if __name__ == "__main__":
    main()
