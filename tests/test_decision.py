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
    service = make_service(tmp_path, fake)
    service.judge_local(ORIGINAL)                          # 설정대로: 기밀만 (남은 개인정보는 이름 인식 모델, D23)
    r = service.judge_local(ORIGINAL, ["has_pii", "is_confidential"])   # 가명화 부품이 없을 때(M1)는 has_pii도 묻는다

    first, req = fake.requests
    assert list(json.loads(first.content)["questions"]) == ["is_confidential"]
    body = json.loads(req.content)
    assert (req.url.host, req.url.port, req.url.path) == ("127.0.0.1", 11435, "/v1/systemone")
    assert body["state"] == ORIGINAL and body["model"] == "gliclass:v4-edge"
    assert list(body["questions"]) == ["has_pii", "is_confidential"]
    assert r.judgments["has_pii"].value is True            # 0.10 ≥ 기준값 0.09
    assert r.judgments["is_confidential"].value is False   # 0.05 < 기준값 0.10
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


def test_too_long_input_is_reported_as_too_long_without_the_body():
    """판단 모델이 읽을 수 있는 길이를 넘으면(Ollaya 422 STATE_TRUNCATED, #39) 로그에 이유가 보이게. 본문은 남기지 않는다."""
    from gateway.decision.engines import TOO_LONG, DecisionError

    def handler(request):
        return httpx.Response(422, json={"error": "state: part of state was dropped to fit the context of gliclass:v4-edge",
                                         "code": "STATE_TRUNCATED", "detail": [{"msg": "비밀 문장이 여기 있다고 치자"}]})

    engine = SystemOneEngine("http://127.0.0.1:11435", "gliclass:v4-edge", 5, transport=httpx.MockTransport(handler))
    with pytest.raises(DecisionError) as e:
        engine.decide("아주 긴 문서", {"is_confidential": QUESTIONS["is_confidential"]})
    assert TOO_LONG in str(e.value) and "STATE_TRUNCATED" in str(e.value)
    assert "비밀 문장" not in str(e.value)


def test_other_422_keeps_the_generic_http_error():
    def handler(request):
        return httpx.Response(422, json={"error": "bad questions", "code": "INVALID_QUESTION"})

    engine = SystemOneEngine("http://127.0.0.1:11435", "gliclass:v4-edge", 5, transport=httpx.MockTransport(handler))
    from gateway.decision.engines import TOO_LONG, DecisionError
    with pytest.raises(DecisionError) as e:
        engine.decide("요청", {"is_confidential": QUESTIONS["is_confidential"]})
    assert "422" in str(e.value) and TOO_LONG not in str(e.value)


def test_confidence_formula():
    assert confidence({"yes": 0.5}) == 0
    assert confidence({"yes": 0.0}) == 1
    assert confidence({"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}) == 0


# ── 판단 모델 미리 올리기 (#84) ──

def test_preload_sends_the_official_load_request():
    """Ollaya 공식 문서(docs/api.md §7.3): state·questions 없이 model·keep_alive만 보내면 모델을 올린다."""
    import httpx
    from gateway.decision.engines import SystemOneEngine
    seen = []

    def handler(request):
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"model": "gliclass:v4-edge", "done_reason": "load"})

    eng = SystemOneEngine("http://127.0.0.1:11435", "gliclass:v4-edge", 10, transport=httpx.MockTransport(handler))
    assert eng.preload("30m") == "load"
    assert seen == [("/api/decide", {"model": "gliclass:v4-edge", "keep_alive": "30m"})]


def test_preload_is_skipped_when_disabled_or_not_ollaya_and_errors_are_decision_errors(tmp_path):
    import httpx
    from gateway.decision.engines import DecisionError, SystemOneEngine
    from gateway.decision.service import DecisionService
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(503)

    settings = load_settings(ROOT / "config.yaml", env_file=tmp_path / "없는.env")
    cfg = settings.config.decision
    local = SystemOneEngine(cfg.base_url, cfg.model, 10, transport=httpx.MockTransport(handler))
    svc = DecisionService(local, local, cfg)
    with pytest.raises(DecisionError):                    # 서버가 실패하면 판단 오류로 (게이트웨이는 경고만 남긴다)
        svc.preload_local()
    for change in ({"preload_keep_alive": None}, {"engine": "jev"}):
        assert DecisionService(local, local, cfg.model_copy(update=change)).preload_local() is None
    assert calls == ["/api/decide"]
