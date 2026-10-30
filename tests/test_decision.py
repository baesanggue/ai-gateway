"""판단 클라이언트 검사 (#8). 실제 서버 대신 httpx.MockTransport로 엔진 응답을 흉내 낸다.

가장 중요한 검사: 클라우드 요청에는 가명화된 문장만 들어가고 원문은 들어가지 않는다 (설계 결정 1·2번).
"""
import json
from pathlib import Path

import httpx
import pytest

from gateway.config import load_settings
from gateway.decision.engines import SystemOneEngine
from gateway.decision.questions import QUESTIONS
from gateway.decision.service import DecisionService, confidence

ROOT = Path(__file__).resolve().parent.parent
TEST_KEY = "sk-test-only-not-a-real-key"
ORIGINAL = "김민수 고객(010-1234-5678)에게 배송 지연 사과 메일 써줘"
MASKED = "[고객1]([전화번호1])에게 배송 지연 사과 메일 써줘"


def predicate(name, p):
    return {"type": "predicate", "name": name, "probability": p}


def refusal(name):
    return {"type": "refusal", "name": name}


def difficulty(p_complex):
    return {"type": "score", "name": "difficulty", "score": p_complex, "confidence": 0.5,
            "probabilities": [{"value": 0, "label": "simple", "probability": 1 - p_complex},
                              {"value": 1, "label": "complex", "probability": p_complex}]}


def intent(**probs):
    return {"type": "choice", "name": "intent", "choice": max(probs, key=probs.get), "confidence": 0.5,
            "probabilities": [{"value": k, "probability": v} for k, v in probs.items()]}


class FakeEngines:
    """요청을 기록하고, 질문 이름에 맞는 답을 돌려준다. cloud_rounds[i]는 i번째 클라우드 요청의 답."""

    def __init__(self, local=None, cloud_rounds=None, cloud_error=None):
        self.local = local or {"has_pii": {"type": "noul", "noul": 0.10},
                               "is_confidential": {"type": "noul", "noul": 0.05}}
        self.cloud_rounds = cloud_rounds or [{}]
        self.cloud_error = cloud_error
        self.requests: list[httpx.Request] = []

    def cloud_bodies(self):
        return [json.loads(r.content) for r in self.requests if r.url.host == "api.openai.com"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        if request.url.path == "/v1/systemone":
            return httpx.Response(200, json={"model": body["model"], "usage": {"input_tokens": 50},
                                             "answers": {q: self.local[q] for q in body["questions"]}})
        if self.cloud_error:
            return self.cloud_error(request)
        answers = self.cloud_rounds[len(self.cloud_bodies()) - 1]
        defaults = {"needs_raw_value": predicate("needs_raw_value", 0.0), "is_risky": predicate("is_risky", 0.0),
                    "difficulty": difficulty(0.01), "intent": intent(faq=0.1, service=0.7, task=0.1, off_topic=0.1)}
        return httpx.Response(200, json={"model": "gpt-6-luna", "usage": {"input_tokens": 120},
                                         "answers": [answers.get(q["name"], defaults[q["name"]])
                                                     for q in body["questions"]]})


def make_service(tmp_path, fake, key=TEST_KEY):
    env = tmp_path / ".env"
    env.write_text(f"OPENAI_API_KEY={key}\n", encoding="utf-8")
    settings = load_settings(ROOT / "config.yaml", env_file=env)
    return DecisionService.from_settings(settings, transport=httpx.MockTransport(fake))


def test_local_sends_original_text_to_systemone_and_applies_thresholds(tmp_path):
    fake = FakeEngines()
    r = make_service(tmp_path, fake).judge_local(ORIGINAL)

    (req,) = fake.requests
    body = json.loads(req.content)
    assert (req.url.host, req.url.port, req.url.path) == ("127.0.0.1", 11435, "/v1/systemone")
    assert body["state"] == ORIGINAL and body["model"] == "gliclass:v4-edge"
    assert list(body["questions"]) == ["has_pii", "is_confidential"]
    assert r.judgments["has_pii"].value is True            # 0.10 ≥ 기준값 0.09
    assert r.judgments["is_confidential"].value is False   # 0.05 < 기준값 0.12
    assert r.engine == "local" and r.available


def test_cloud_receives_only_masked_text(tmp_path):
    fake = FakeEngines()
    make_service(tmp_path, fake).judge_cloud(MASKED, has_masked_pii=True)

    (body,) = fake.cloud_bodies()
    assert body["input"] == MASKED
    sent = json.dumps(body, ensure_ascii=False)
    assert "김민수" not in sent and "010-1234-5678" not in sent
    assert fake.requests[0].headers["Authorization"] == f"Bearer {TEST_KEY}"


def test_needs_raw_value_is_asked_only_when_masked_pii_exists(tmp_path):
    fake = FakeEngines(cloud_rounds=[{}, {}])
    svc = make_service(tmp_path, fake)
    without = svc.judge_cloud("주문한 거 배송 언제 와요?", has_masked_pii=False)
    with_pii = svc.judge_cloud(MASKED, has_masked_pii=True)

    names = [[q["name"] for q in b["questions"]] for b in fake.cloud_bodies()]
    assert names[0] == ["is_risky", "difficulty", "intent"]
    assert names[1] == ["needs_raw_value", "is_risky", "difficulty", "intent"]
    assert "needs_raw_value" not in without.judgments and "needs_raw_value" in with_pii.judgments


def test_cloud_answers_are_normalized_with_thresholds(tmp_path):
    fake = FakeEngines(cloud_rounds=[{"is_risky": predicate("is_risky", 0.9), "difficulty": difficulty(0.1)}])
    r = make_service(tmp_path, fake).judge_cloud(MASKED, has_masked_pii=True)

    assert r.judgments["is_risky"].value is True
    assert r.judgments["difficulty"].value == "complex"    # P(복잡) 0.1 ≥ 기준값 0.08
    assert r.judgments["intent"].value == "work"           # service → work 로 묶음
    assert r.judgments["intent"].confidence == pytest.approx((4 * 0.7 - 1) / 3, abs=1e-4)
    assert r.input_tokens == 120


def test_refused_questions_are_marked_and_not_asked_again(tmp_path):
    # 다시 물어도 26문장 모두 똑같이 거부했다 (#8) → 거부는 표시만 하고, 처리는 라우팅이 한다
    fake = FakeEngines(cloud_rounds=[{"difficulty": refusal("difficulty"), "is_risky": refusal("is_risky")}])
    r = make_service(tmp_path, fake).judge_cloud(MASKED, has_masked_pii=True)

    assert len(fake.cloud_bodies()) == 1
    for q in ("difficulty", "is_risky"):
        j = r.judgments[q]
        assert j.refused and j.value is None and j.confidence is None
    assert r.judgments["intent"].value == "work" and r.available


def raise_timeout(request):
    raise httpx.ReadTimeout("timed out", request=request)


@pytest.mark.parametrize("error", [
    raise_timeout,
    lambda req: httpx.Response(500, json={"error": {"message": "server error"}}),
    lambda req: httpx.Response(200, json={"unexpected": True}),
], ids=["timeout", "http-500", "unknown-format"])
def test_cloud_failure_is_returned_as_error_not_raised(tmp_path, error):
    fake = FakeEngines(cloud_error=error)
    r = make_service(tmp_path, fake).judge_cloud(MASKED, has_masked_pii=True)

    assert not r.available and r.judgments == {}
    assert TEST_KEY not in r.error and MASKED not in r.error


def test_service_refuses_to_start_without_api_key(tmp_path):
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        make_service(tmp_path, FakeEngines(), key="")


def test_systemone_score_probabilities_are_mapped_to_level_labels():
    def handler(request):
        return httpx.Response(200, json={"answers": {"difficulty": {
            "type": "score", "score": 0.7, "confidence": 0.4, "probabilities": {"0": 0.3, "1": 0.7}}}})

    engine = SystemOneEngine("http://127.0.0.1:8100", "local-ml", 5, transport=httpx.MockTransport(handler))
    r = engine.decide("보고서 써줘", {"difficulty": QUESTIONS["difficulty"]})
    assert r.answers["difficulty"].probs == {"simple": 0.3, "complex": 0.7}


def test_confidence_formula():
    assert confidence({"yes": 0.5}) == 0
    assert confidence({"yes": 0.0}) == 1
    assert confidence({"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}) == 0
