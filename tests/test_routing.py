"""라우팅 정책 검사 (#10). 판단 결과(Judgment)를 직접 만들어 엔진 없이 경로만 본다. 설정은 config.yaml."""
from pathlib import Path

import pytest

from gateway.config import load_settings
from gateway.decision.service import JudgeResult, Judgment
from gateway.routing.policy import BLOCK_PRIORITY, decide_after_cloud, decide_after_local, match_faq

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg(tmp_path):
    return load_settings(ROOT / "config.yaml", env_file=tmp_path / "없는.env").config


def noul(qid, value, conf=0.9):
    return Judgment(qid, {"yes": 0.95 if value else 0.01}, value, conf)


def refused(qid):
    return Judgment(qid, None, None, None)


def difficulty(level, conf=0.9):
    return Judgment("difficulty", {"simple": 0.5, "complex": 0.5}, level, conf)


def intent(group, conf=0.9):
    return Judgment("intent", {"faq": 0.25, "service": 0.25, "task": 0.25, "off_topic": 0.25}, group, conf)


def judged(engine, *judgments):
    return JudgeResult(engine, "test-model", {j.question: j for j in judgments})


def cloud(*extra, risky=False, diff="simple", purpose="work"):
    """기본은 위험 아님 · 단순 · 업무. 바꿀 판단만 인자로 준다."""
    base = {"is_risky": noul("is_risky", risky), "difficulty": difficulty(diff), "intent": intent(purpose)}
    base |= {j.question: j for j in extra}
    return JudgeResult("cloud", "test-model", base)


# ── FAQ (판단 전, 코드) ──────────────────────────────────────

def test_demo_scenario_3_is_answered_by_template(cfg):
    d = match_faq("주문한 거 배송 언제 와요?", cfg.faq)
    assert (d.route, d.reasons, d.faq_id) == ("template", ["faq"], "shipping")
    assert d.message == next(e.answer for e in cfg.faq.entries if e.id == "shipping")


@pytest.mark.parametrize("text, faq_id", [
    ("배송언제와요", "shipping"),                    # 띄어쓰기를 무시한다
    ("사이즈 안 맞는데 교환돼요?", "return_exchange"),
    ("결제 수단 뭐 있어요?", "payment"),
    ("주말에도 상담 되나요?", "cs_hours"),
])
def test_registered_faq_variants_match(cfg, text, faq_id):
    assert match_faq(text, cfg.faq).faq_id == faq_id


@pytest.mark.parametrize("text", [
    "김민수 고객(010-1234-5678)에게 배송 지연 사과 메일 써줘",        # 업무 요청 표현 (시연 4번)
    "교환·반품 정책 초안을 전자상거래법 기준으로 작성해 줘",          # 업무 요청 표현
    "지난달 반품 사유를 보면 사이즈가 40%인데 반품을 줄일 개선안을 우선순위와 함께 알려 줘",  # 너무 긺
    "오늘 날씨 어때?",                                                # 등록된 FAQ 아님
    "신입 CS 상담원 교육 커리큘럼을 4주 과정으로 짜 줘",              # 190행 확인에서 잡힌 오탐 (10/31)
])
def test_non_faq_requests_are_not_matched(cfg, text):
    assert match_faq(text, cfg.faq) is None


# ── 로컬 판단 뒤 (엄격 수준에서만 차단) ──────────────────────

def test_basic_level_never_blocks_after_local(cfg):
    local = judged("local", noul("has_pii", True), noul("is_confidential", True))
    assert decide_after_local(local, "basic", cfg.routing.block_messages) is None


@pytest.mark.parametrize("level", ["strict", "local"])   # 완전 로컬은 구현 전까지 엄격처럼
def test_strict_blocks_confidential_before_cloud(cfg, level):
    local = judged("local", noul("has_pii", False), noul("is_confidential", True))
    d = decide_after_local(local, level, cfg.routing.block_messages)
    assert (d.route, d.reasons) == ("block", ["confidential"])
    assert d.message == cfg.routing.block_messages["confidential"]


def test_strict_records_every_local_reason(cfg):
    local = judged("local", noul("has_pii", True), noul("is_confidential", True))
    d = decide_after_local(local, "strict", cfg.routing.block_messages)
    assert d.reasons == ["confidential", "pii_residual"]


def test_strict_passes_clean_request_to_cloud(cfg):
    local = judged("local", noul("has_pii", False), noul("is_confidential", False))
    assert decide_after_local(local, "strict", cfg.routing.block_messages) is None


def test_local_engine_failure_blocks_only_in_strict(cfg):
    failed = JudgeResult("local", "test-model", error="ConnectError")
    assert decide_after_local(failed, "basic", cfg.routing.block_messages) is None
    d = decide_after_local(failed, "strict", cfg.routing.block_messages)
    assert (d.route, d.reasons) == ("block", ["local_error"])


# ── 클라우드 판단 뒤 ─────────────────────────────────────────

@pytest.mark.parametrize("result, route, reasons", [
    (cloud(), "cheap", ["simple"]),
    (cloud(diff="complex"), "premium", ["complex"]),
    (cloud(purpose="faq"), "cheap", ["faq_unregistered"]),           # FAQ 의도지만 등록된 답이 없음
    (cloud(risky=True), "block", ["risky"]),
    (cloud(noul("needs_raw_value", True)), "block", ["needs_raw"]),  # 시연 5번
    (cloud(purpose="off_topic"), "block", ["off_topic"]),
    (cloud(refused("is_risky")), "block", ["refused"]),               # 위험 판단 거부 → 차단 (D16)
    (cloud(refused("difficulty")), "premium", ["routing_refused"]),   # 난이도 판단 거부 → 고성능 (D16)
    (cloud(refused("intent")), "premium", ["routing_refused"]),
    (JudgeResult("cloud", "test-model", error="ReadTimeout"), "cheap", ["cloud_error"]),
], ids=["simple", "complex", "faq-intent", "risky", "needs-raw", "off-topic",
        "risky-refused", "difficulty-refused", "intent-refused", "cloud-error"])
def test_cloud_routes(cfg, result, route, reasons):
    d = decide_after_cloud(result, cfg)
    assert (d.route, d.reasons) == (route, reasons)
    if route == "block":
        assert d.message == cfg.routing.block_messages[reasons[0]]
    else:
        assert d.message is None


def test_injection_that_is_also_off_topic_shows_both_but_explains_risk(cfg):
    d = decide_after_cloud(cloud(risky=True, purpose="off_topic"), cfg)
    assert d.reasons == ["risky", "off_topic"]
    assert d.message == cfg.routing.block_messages["risky"]


def test_definite_block_wins_over_routing_refusal(cfg):
    d = decide_after_cloud(cloud(noul("needs_raw_value", True), refused("difficulty")), cfg)
    assert (d.route, d.reasons) == ("block", ["needs_raw"])


def test_low_confidence_goes_premium_and_does_not_block_off_topic(cfg):
    cfg.routing.min_confidence = 0.5
    assert decide_after_cloud(cloud(difficulty("simple", conf=0.2)), cfg).reasons == ["low_confidence"]
    d = decide_after_cloud(cloud(intent("off_topic", conf=0.2)), cfg)
    assert (d.route, d.reasons) == ("premium", ["low_confidence"])   # 애매한 '무관'은 막지 않는다


def test_block_setting_for_routing_refusal_uses_refusal_message(cfg):
    cfg.decision.cloud.on_routing_refusal = "block"
    d = decide_after_cloud(cloud(refused("difficulty")), cfg)
    assert (d.route, d.reasons, d.message) == ("block", ["refused"], cfg.routing.block_messages["refused"])


def test_every_block_reason_has_a_message_in_config(cfg):
    assert set(BLOCK_PRIORITY) <= set(cfg.routing.block_messages)
