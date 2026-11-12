"""시연 화면의 "게이트웨이가 한 일" 다섯 단계 (#76). 요청 로그 한 줄의 모양(RequestLog.get)으로 시험한다 (화면 없이)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from admin.flow import answer_html, build_steps, outbound_text, session_totals  # noqa: E402

TH = {"is_confidential": 0.10, "needs_raw_value": 0.02, "is_risky": 0.5}


def judge(engine, **qs):
    return {"engine": engine, "model": "m", "latency_ms": 200.0, "error": None, "parts": 1, "cached_parts": 0,
            "judgments": qs}


def noul(p, value):
    return {"value": value, "confidence": abs(2 * p - 1), "refused": False, "probs": {"yes": p}}


LOCAL_OK = judge("local", is_confidential=noul(0.03, False))
CLOUD_SIMPLE = judge("cloud", needs_raw_value=noul(0.0, False), is_risky=noul(0.0, False),
                     difficulty={"value": "simple", "confidence": 0.9, "refused": False, "probs": {}},
                     intent={"value": "work", "confidence": 0.95, "refused": False, "probs": {}})


def row(**kw):
    base = {"route": "cheap", "reasons": ["simple"], "pii_counts": {}, "judgments": {}, "text_masked": "요청",
            "model": "gpt-6-luna", "cost_usd": 0.0001, "baseline_cost_usd": 0.002, "prompt_tokens": 50,
            "completion_tokens": 80, "total_latency_ms": 2400.0, "unrestored_tokens": 0}
    return base | kw


def test_faq_template_skips_judgment_and_sends_nothing_out():
    r = row(route="template", reasons=["faq"], model=None, cost_usd=0.0, text_masked="배송 언제 와요?")
    steps = build_steps(r, TH)
    assert [s.status for s in steps] == ["done", "skipped", "skipped", "done", "skipped"]
    assert "FAQ" in steps[1].summary and "비용 0" in steps[3].summary
    assert outbound_text(r) is None


def test_masked_request_to_cheap_model_shows_counts_probability_over_threshold_and_restore():
    r = row(pii_counts={"고객": 1, "전화번호": 1}, text_masked="[고객1] 고객([전화번호1])에게 사과 메일 써줘",
            judgments={"local": LOCAL_OK, "cloud": CLOUD_SIMPLE})
    steps = build_steps(r, TH)
    assert steps[0].summary == "개인정보 2개를 가렸습니다 (고객 1, 전화번호 1)"
    assert ("기밀", "아니오 (0.03 / 기준 0.10)") in steps[1].details           # D24: 확률 / 기준값
    assert steps[2].summary == "단순한 요청 · 업무"
    assert "저렴한 모델(gpt-6-luna)" in steps[3].summary and "되돌렸습니다" in steps[4].summary
    assert outbound_text(r) == "[고객1] 고객([전화번호1])에게 사과 메일 써줘"


def test_needs_raw_value_is_blocked_at_the_cloud_step():
    cloud = judge("cloud", needs_raw_value=noul(0.98, True), is_risky=noul(0.0, False))
    r = row(route="block", reasons=["needs_raw"], pii_counts={"주민번호": 1}, model=None, cost_usd=0.0,
            judgments={"local": LOCAL_OK, "cloud": cloud})
    steps = build_steps(r, TH)
    assert steps[2].status == "blocked" and "실제 값이 필요" in steps[2].summary
    assert steps[3].status == "blocked" and steps[4].status == "skipped"


def test_strict_confidential_block_stops_before_the_cloud():
    local = judge("local", is_confidential=noul(0.39, True))
    r = row(route="block", reasons=["confidential"], judgments={"local": local}, model=None, cost_usd=0.0)
    steps = build_steps(r, TH)
    assert steps[1].status == "blocked" and "사내 기밀" in steps[1].summary
    assert steps[2].status == "skipped" and outbound_text(r) is None


def test_local_failure_in_basic_continues_and_shows_the_reason():
    local = judge("local") | {"error": "SystemOneEngine: 입력이 판단 모델이 읽을 수 있는 길이를 넘음 (422 STATE_TRUNCATED)"}
    r = row(judgments={"local": local, "cloud": CLOUD_SIMPLE})
    steps = build_steps(r, TH)
    assert "기본 수준이라 진행" in steps[1].summary
    assert any(label == "오류" and "길이를 넘음" in value for label, value in steps[1].details)


def test_unmasked_notation_is_blocked_at_the_masking_step():
    r = row(route="block", reasons=["pii_unmasked"], judgments={}, text_masked=None, model=None, cost_usd=0.0)
    steps = build_steps(r, TH)
    assert steps[0].status == "blocked" and "가리지 못한 표기" in steps[0].summary
    assert ("밖으로 나갈 문장", "(가리지 못한 값이 있어 로그에 남기지 않음)") in steps[0].details


def test_session_totals_sum_cost_and_baseline():
    assert session_totals([row(), row(cost_usd=0.0, baseline_cost_usd=0.001)]) == (0.0001, 0.003)


def test_answer_html_shows_bold_without_running_html():
    """LLM 답의 **굵게**는 굵게 보이고 별표는 사라진다 (#80 리허설: 시연 화면에 **가 보였다).
    답 속 HTML은 먼저 이스케이프되어 화면에서 동작하지 않는다."""
    assert answer_html("**제목: 사과 말씀**\n김민수 고객님께") == "<b>제목: 사과 말씀</b>\n김민수 고객님께"
    assert answer_html("**<script>x</script>**") == "<b>&lt;script&gt;x&lt;/script&gt;</b>"
    assert answer_html("2 ** 3 = 8, a**b") == "2 ** 3 = 8, a**b"          # 짝이 없거나 공백으로 감싼 별표는 그대로
    assert answer_html("**줄이\n넘어가면**") == "**줄이\n넘어가면**"       # 굵게는 한 줄 안에서만
