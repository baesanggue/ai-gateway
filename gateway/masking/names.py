"""사전에 없는 이름 찾기 (#53).

가명화(사전 + 정규식) 뒤의 문장에서
1. **코드**가 이름 후보를 뽑는다: 성씨로 시작하는 2~4글자 한글 낱말. 뒤에 붙은 조사·호칭은 떼어 낸다
2. **로컬 판단 모델**이 후보마다 "이 요청에서 '<후보>'는 사람 이름인가?"만 판단한다
3. 이름이면 가명화 부품이 토큰으로 바꾼다 (위치는 코드가 안다 → 막지 않고 가릴 수 있다)

판단 모델에 문장 전체를 주고 "개인정보가 남았나?"를 물으면 [고객1] 같은 토큰과 "카드번호" 같은
낱말에 반응했다(#46: 다 가려진 문장의 95%를 '남았다'). 그래서 질문을 낱말 하나로 좁힌다.
설계 결정 1번(위치는 코드, 판단은 판단 모델)과 같다.
"""
import re
import time
from dataclasses import dataclass, field

from gateway.decision.engines import DecisionError, Engine
from gateway.decision.questions import Question

# 한국 성씨 (흔한 것부터). 드문 성씨는 빠질 수 있다 → 놓친 이름으로 측정에 드러난다
SURNAMES_1 = set("김이박최정강조윤장임한오서신권황안송류유전홍고문양손배백허남심노하곽성차주우구민진나지엄채원천방"
                 "공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예경봉사부가복태목형계피두감"
                 "음빈동온호범좌팽승간상시갈단견당")
SURNAMES_2 = ("남궁", "황보", "제갈", "사공", "선우", "서문", "독고", "동방")
# 이름 뒤에 붙는 말 (긴 것부터 뗀다). 호칭 + 조사가 겹치면 두 번 뗀다 ("김민수님께" → "김민수님" → "김민수")
SUFFIXES = sorted(set(
    "님 씨 군 고객 환자 선생님 학생 과장 대리 부장 팀장 주임 사원 교수 원장 실장 민원인 담당 담당자 "
    "에게서 한테서 께서 에게 한테 께 으로 로 에서 까지 부터 이랑 랑 하고 처럼 보다 과 와 은 는 이 가 을 를 의 도 만 야 아 요".split()),
    key=len, reverse=True)
_WORD = re.compile(r"[가-힣]+")
_TOKEN = re.compile(r"\[[^\[\]\n]{1,30}\]")         # 이미 가린 토큰 [고객1] 안의 "고객"은 후보가 아니다
NAME_QUESTION = "이 요청에서 '{}'는 사람 이름인가?"


@dataclass(frozen=True)
class Candidate:
    """한 낱말에서 나온 이름 모양들. 이름 끝 글자가 조사와 같을 수 있어("박지은" → "박지" + "은") 모두 묻는다."""
    options: tuple[str, ...]      # 긴 것부터 ("박지은", "박지")
    start: int                    # 낱말 시작 위치 (이름은 늘 낱말 앞에서 시작한다)


def is_name_shape(stem: str) -> bool:
    if stem[:2] in SURNAMES_2:
        return 1 <= len(stem) - 2 <= 2
    return stem[:1] in SURNAMES_1 and 1 <= len(stem) - 1 <= 2


def _stems(word: str) -> list[str]:
    """낱말과, 뒤에 붙은 말을 한두 번 뗀 것들."""
    out, frontier = [word], [word]
    for _ in range(2):
        nxt = [w[: -len(s)] for w in frontier for s in SUFFIXES if w.endswith(s) and len(w) > len(s)]
        out += nxt
        frontier = nxt
    return out


def candidates(text: str) -> list[Candidate]:
    """이름 후보 (나온 순서). 이미 가린 토큰 안은 보지 않는다."""
    blanked = _TOKEN.sub(lambda m: " " * len(m.group()), text)
    out = []
    for m in _WORD.finditer(blanked):
        shaped = sorted({s for s in _stems(m.group()) if is_name_shape(s)}, key=len, reverse=True)
        if shaped:
            out.append(Candidate(tuple(shaped), m.start()))
    return out


@dataclass
class NameResult:
    names: list[str] = field(default_factory=list)          # 사람 이름으로 판단한 값 (서로 다른 값, 나온 순서)
    probs: dict[str, float] = field(default_factory=dict)   # 물어본 이름 모양 → P(사람 이름)
    latency_ms: float = 0.0
    error: str | None = None


class NameFinder:
    """후보를 한 번의 요청으로 묻는다 (이름 모양마다 질문 하나). 낱말마다 이름으로 판단된 가장 긴 모양을 고른다."""

    def __init__(self, engine: Engine, threshold: float):
        self.engine = engine
        self.threshold = threshold

    def find(self, text: str) -> NameResult:
        found = candidates(text)
        values = list(dict.fromkeys(v for c in found for v in c.options))
        if not values:
            return NameResult()
        questions = {f"name{i}": Question("noul", NAME_QUESTION.format(v)) for i, v in enumerate(values)}
        t0 = time.perf_counter()
        try:
            r = self.engine.decide(text, questions)
        except DecisionError as e:
            return NameResult(error=str(e), latency_ms=(time.perf_counter() - t0) * 1000)
        probs = {v: r.answers[f"name{i}"].probs["yes"] for i, v in enumerate(values)}
        names = [next((v for v in c.options if probs[v] >= self.threshold), None) for c in found]
        return NameResult(list(dict.fromkeys(n for n in names if n)), probs, r.latency_ms)
