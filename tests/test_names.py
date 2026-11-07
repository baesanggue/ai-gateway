"""사전에 없는 이름 찾기 (#53): 코드 후보 추출 + 판단 모델의 후보별 판단. 판단 엔진은 가짜를 쓴다."""
import re

from gateway.decision.engines import DecisionError, EngineAnswer, EngineResult
from gateway.masking.names import NameFinder, candidates, is_name_shape


def options(text):
    return [c.options for c in candidates(text)]


def test_name_shape():
    assert is_name_shape("김민수") and is_name_shape("왕휘") and is_name_shape("남궁민") and is_name_shape("황보혜인")
    assert not is_name_shape("김") and not is_name_shape("김수한무")    # 한 글자 성 + 세 글자 이름은 보지 않는다
    assert not is_name_shape("묵하진")                                   # 성씨 목록에 없는 성 (알려진 한계)


def test_candidates_strip_particles_and_titles():
    assert ("양미래",) in options("민원인 이름 양미래를 한자로 바꿔 줘")
    assert ("남궁민님", "남궁민") in options("남궁민님께 보내 줘")          # 호칭 + 조사를 두 번 뗀다
    assert ("박지은", "박지") in options("박지은 고객")                     # 이름 끝 글자가 조사와 같으면 둘 다 묻는다


def test_candidates_skip_existing_tokens():
    """이미 가린 [고객1] 안의 "고객"은 후보가 아니다. 토큰 밖의 낱말 "고객"은 후보다 (판단 모델이 가른다)."""
    found = candidates("[고객1] 고객([전화번호1])에게")
    assert [c.options for c in found] == [("고객",)] and found[0].start == 6   # "[고객1] " 다음


class FakeEngine:
    model = "fake"

    def __init__(self, probs: dict[str, float], fail: bool = False):
        self.probs, self.fail, self.calls = probs, fail, []

    def decide(self, text, questions):
        self.calls.append(questions)
        if self.fail:
            raise DecisionError("timeout")
        value = {qid: re.search(r"'(.+?)'", q.instructions).group(1) for qid, q in questions.items()}
        return EngineResult({qid: EngineAnswer({"yes": self.probs.get(v, 0.0)}) for qid, v in value.items()}, 3.0, 0)

    def close(self):
        pass


def test_finder_picks_longest_option_judged_as_name():
    engine = FakeEngine({"박지은": 0.9, "박지": 0.8, "고객": 0.1})
    r = NameFinder(engine, 0.5).find("박지은 고객 문의")
    assert r.names == ["박지은"] and r.probs["고객"] == 0.1
    assert len(engine.calls) == 1 and len(engine.calls[0]) == 4      # 이름 모양 4개를 한 번의 요청으로


def test_finder_falls_back_to_shorter_option():
    assert NameFinder(FakeEngine({"박지은": 0.1, "박지": 0.8}), 0.5).find("박지은 고객").names == ["박지"]


def test_finder_without_candidates_does_not_call_engine():
    engine = FakeEngine({})
    assert NameFinder(engine, 0.5).find("[고객1]에게 보내 줘").names == [] and engine.calls == []


def test_finder_reports_engine_error():
    r = NameFinder(FakeEngine({}, fail=True), 0.5).find("양미래 고객")
    assert r.error == "timeout" and r.names == []
