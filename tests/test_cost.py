"""비용 계산 검사 (#9). 가격은 config.yaml 값: luna $0.10/$0.50, sol $2.00/$10.00 (100만 토큰당)."""
from pathlib import Path

import pytest

from gateway.config import load_settings
from gateway.llm.client import LLMUsage
from gateway.llm.cost import baseline_cost, request_cost

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def llm(tmp_path):
    return load_settings(ROOT / "config.yaml", env_file=tmp_path / "없는.env").config.llm


def test_request_cost_uses_input_and_output_prices(llm):
    # 시연 4번 실제 호출: 입력 122, 출력 120 (추론 토큰은 출력에 포함)
    assert request_cost(LLMUsage(122, 120), llm.cheap) == pytest.approx((122 * 0.10 + 120 * 0.50) / 1e6)
    assert request_cost(LLMUsage(122, 120), llm.premium) == pytest.approx((122 * 2.00 + 120 * 10.00) / 1e6)


def test_baseline_for_premium_route_equals_actual_cost(llm):
    usage = LLMUsage(134, 175)
    assert baseline_cost("premium", usage, "보고서 써 줘", llm) == request_cost(usage, llm.premium)


def test_baseline_for_cheap_route_reprices_same_tokens_at_premium(llm):
    usage = LLMUsage(134, 113)
    baseline = baseline_cost("cheap", usage, "사과 메일 써 줘", llm)
    assert baseline == pytest.approx((134 * 2.00 + 113 * 10.00) / 1e6)   # 출력 배수 1 (보수적)
    assert baseline > request_cost(usage, llm.cheap)                       # 절감액은 양수


def test_baseline_for_template_route_estimates_tokens(llm):
    text = "주문한 거 배송 언제 와요?"                                         # 15자 → 입력 약 12토큰
    assert baseline_cost("template", None, text, llm) == pytest.approx((12 * 2.00 + 175 * 10.00) / 1e6)


def test_blocked_request_is_not_counted_as_saving(llm):
    assert baseline_cost("block", None, "이 주민번호로 고객 나이 계산해줘", llm) == 0.0
