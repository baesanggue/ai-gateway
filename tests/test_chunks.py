"""로컬 판단을 구간으로 나누기 (#71). 판단 모델 길이(#39)를 넘지 않고, 대화가 길어져도 앞 구간이 바뀌지 않아야 기억이 통한다."""
import json

import httpx

from gateway.config import DecisionConfig
from gateway.decision.chunks import chunks, halves, split_long
from gateway.decision.engines import SystemOneEngine
from gateway.decision.service import DecisionService

SENTENCE = "고객 문의 내역을 정리해 주세요. 지난주 배송 지연으로 불편을 드린 고객님께 사과 안내를 드렸습니다. "


def test_long_text_is_split_within_size_with_overlap_and_nothing_lost():
    text = "".join(f"{i}번 문의를 정리해 주세요. 배송 지연 사과 안내를 드렸습니다. " for i in range(60))   # 문장마다 달라 위치가 하나
    parts = split_long(text, 1000, 100)
    assert len(parts) >= 3 and all(len(p) <= 1000 for p in parts)
    assert parts[0].endswith(". ")                                    # 문장 끝에서 자른다
    starts = [text.index(p) for p in parts]
    ends = [s + len(p) for s, p in zip(starts, parts)]
    assert starts[0] == 0 and ends[-1] == len(text)                   # 처음부터 끝까지
    assert all(starts[i + 1] < ends[i] for i in range(len(parts) - 1))   # 이웃 구간이 겹친다 (빈틈 없음)


def test_short_messages_are_packed_and_earlier_chunks_do_not_change_when_the_chat_grows():
    system = "너는 쇼핑몰 상담원이다. " * 60                              # 약 840자 지시문
    turn1 = [system, "배송 언제 와요?"]
    turn2 = turn1 + ["네 알겠습니다. 그럼 교환은요?", "교환 접수 방법 알려 줘"]
    c1, c2 = chunks(turn1, 1000, 100), chunks(turn2, 1000, 100)
    assert c1 == [system + "\n배송 언제 와요?"]                         # 짧으면 묶어서 판단 한 번
    long_system = system * 3                                          # 약 2,500자 → 나뉜다
    d1, d2 = chunks([long_system, "배송 언제 와요?"], 1000, 100), chunks([long_system, "배송 언제 와요?", "교환은요?"], 1000, 100)
    assert d1[:-1] == d2[:-1] and all(len(p) <= 1000 for p in d1 + d2)   # 끝 구간만 바뀐다
    assert chunks([], 1000, 100) == [""]


def test_halves_cut_near_the_middle_at_a_space():
    a, b = halves("가나다 라마바 사아자 차카타")
    assert a + b == "가나다 라마바 사아자 차카타" and abs(len(a) - len(b)) <= 4


# ── 판단 서비스: 나눠 묻기·합치기·기억 ──────────────────────────

class Local:
    """가짜 로컬 판단 서버. 기밀 문장이 든 구간만 '예', limit보다 긴 state는 422 STATE_TRUNCATED."""

    def __init__(self, limit=1300, refuse=False, fail_on=None):
        self.states: list[str] = []
        self.limit, self.refuse, self.fail_on = limit, refuse, fail_on

    def __call__(self, request):
        state = json.loads(request.content)["state"]
        self.states.append(state)
        if len(state) > self.limit:
            return httpx.Response(422, json={"code": "STATE_TRUNCATED", "error": "dropped"})
        if self.fail_on and self.fail_on in state:
            return httpx.Response(500, json={"error": "boom"})
        if self.refuse:
            return httpx.Response(200, json={"answers": {"is_confidential": {"type": "refusal"}}})
        p = 0.9 if "인수가 850억" in state else 0.02
        return httpx.Response(200, json={"usage": {"input_tokens": len(state)},
                                         "answers": {"is_confidential": {"type": "noul", "noul": p}}})


def service(local: Local, **cfg) -> DecisionService:
    d = DecisionConfig(base_url="http://127.0.0.1:11435", model="gliclass:v4-edge", local_questions=["is_confidential"],
                       cloud={"model": "gpt-6-luna", "questions": []}, thresholds={"is_confidential": 0.1}, **cfg)
    engine = SystemOneEngine(d.base_url, d.model, 5, transport=httpx.MockTransport(local))
    return DecisionService(engine, engine, d)


def test_long_chat_is_judged_in_chunks_and_any_confidential_chunk_counts():
    """긴 system 지시문 속 기밀 문장도 찾는다. 구간마다 판단 모델 길이 안이다."""
    local = Local()
    system = SENTENCE * 20 + "참고: 다음 분기 A사 인수가 850억이다. " + SENTENCE * 20
    r = service(local).judge_local([system, "배송 언제 와요?"])
    assert r.available and r.parts == len(local.states) >= 3
    assert all(len(s) <= 1000 for s in local.states)
    assert r.judgments["is_confidential"].value is True and r.judgments["is_confidential"].probs == {"yes": 0.9}


def test_repeated_chunks_are_remembered_so_the_next_turn_judges_only_new_text():
    local = Local()
    svc = service(local)
    system = SENTENCE * 40
    first = svc.judge_local([system, "배송 언제 와요?"])
    asked = len(local.states)
    second = svc.judge_local([system, "배송 언제 와요?", "그럼 교환은요?"])
    assert second.parts == first.parts and second.cached_parts == second.parts - 1
    assert len(local.states) == asked + 1                             # 새로 물은 것은 끝 구간 하나
    third = svc.judge_local([system, "배송 언제 와요?", "그럼 교환은요?"])
    assert third.cached and len(local.states) == asked + 1             # 전부 기억 → 부르지 않는다


def test_chunk_still_too_long_is_halved_and_asked_again():
    """글자당 토큰이 많은 글은 1,000자 구간도 넘을 수 있다 → 반으로 나눠 다시 묻는다."""
    local = Local(limit=600)
    r = service(local).judge_local(SENTENCE * 15)
    assert r.available and any(len(s) > 600 for s in local.states) and all(
        len(s) <= 600 for s in local.states if s not in local.states[:1])


def test_too_many_new_chunks_fail_without_calling_the_model():
    local = Local()
    r = service(local, local_max_new_chunks=2).judge_local(SENTENCE * 60)
    assert not r.available and "구간" in r.error and local.states == []


def test_failure_in_any_chunk_fails_the_whole_judgment():
    """한 구간이라도 실패하면 전체 실패다. 엄격 수준은 막는다 (D19). 나머지 구간의 '아니오'로 덮지 않는다."""
    r = service(Local(fail_on="배송 언제")).judge_local([SENTENCE * 20, "배송 언제 와요?"])
    assert not r.available and r.parts >= 2


def test_merge_rules():
    """거부가 하나라도 있으면 거부(엔진이 거부를 돌려줄 때, 예: 설정으로 다른 엔진), 예/아니오는 가장 높은 확률, 그 밖은 마지막 구간."""
    from gateway.decision.engines import EngineAnswer
    from gateway.decision.questions import QUESTIONS
    from gateway.decision.service import _merge
    qs = {"is_confidential": QUESTIONS["is_confidential"], "difficulty": QUESTIONS["difficulty"]}
    a = {"is_confidential": EngineAnswer({"yes": 0.05}), "difficulty": EngineAnswer({"simple": 0.2, "complex": 0.8})}
    b = {"is_confidential": EngineAnswer({"yes": 0.6}), "difficulty": EngineAnswer({"simple": 0.9, "complex": 0.1})}
    merged = _merge([a, b], qs)
    assert merged["is_confidential"].probs == {"yes": 0.6} and merged["difficulty"].probs["simple"] == 0.9
    refused = _merge([a, {"is_confidential": EngineAnswer(None), "difficulty": b["difficulty"]}], qs)
    assert refused["is_confidential"].refused
