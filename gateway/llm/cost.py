"""비용 계산 (설계 결정 6번: 계산은 코드에서). 가격은 config.yaml의 llm.*.price_per_1m_*.

- request_cost: 실제로 쓴 비용
- baseline_cost: "전부 고성능 모델로 보냈다면" 들었을 비용. 비용 카운터(시연 6번)의 비교 기준이다.
  게이트웨이 절감액 = Σ baseline_cost − Σ request_cost
판단 비용(Decisions 입력 토큰)은 여기서 계산하지 않는다. 기존 방식에는 없던 비용이라 로그에 따로 남긴다(#11).
"""
from typing import Literal

from gateway.config import LLMConfig, LLMModelConfig
from gateway.llm.client import LLMUsage

Route = Literal["template", "cheap", "premium", "block"]


def request_cost(usage: LLMUsage, model: LLMModelConfig) -> float:
    """입력 토큰 × 입력 가격 + 출력 토큰 × 출력 가격 (USD). 추론 토큰은 completion_tokens에 들어 있다."""
    return (usage.prompt_tokens * model.price_per_1m_input
            + usage.completion_tokens * model.price_per_1m_output) / 1_000_000


def baseline_cost(route: Route, usage: LLMUsage | None, prompt_text: str, cfg: LLMConfig) -> float:
    """이 요청을 기존 방식(무조건 고성능 모델)으로 보냈다면 들었을 비용 (USD).

    route: 게이트웨이가 실제로 고른 경로
    usage: 실제로 LLM을 불렀으면(cheap|premium) 그 사용량, 부르지 않았으면(template|block) None
    prompt_text: 사용자 요청 문장. LLM을 부르지 않았을 때 토큰 수를 추정하는 데 쓸 수 있다
    cfg.premium: 고성능 모델의 가격

    추정 방식 (D18, 근거: bench/results/placeholder-2026-10-30/)
    - premium: 실제로 고성능 모델로 보냈다 → 실제 비용 그대로
    - cheap: 같은 토큰 수에 고성능 가격만 곱한다. 고성능 모델이 출력을 더 길게 쓰는 경향(중앙값 1.48배)은
      반영하지 않는다 → 절감액을 보수적으로 잡는다
    - template: 입력은 글자 수 × 0.786, 출력은 고성능 모델 평균 175토큰으로 추정한다
    - block: 0. 차단은 "절약"이 아니라 "보안" 효과라 절감액에 넣지 않는다
    """
    if route == "premium":
        # 실제로 고성능 모델로 보냈다 → 실제 비용 그대로
        return request_cost(usage, cfg.premium)

    if route == "cheap":
        # 저렴한 모델이 쓴 토큰 수를 고성능 가격으로 다시 계산한다
        # 출력 배수 1: 고성능 모델의 긴 출력(1.48배)은 반영하지 않는다 (보수적)
        output = round(usage.completion_tokens * 1)
        return request_cost(LLMUsage(usage.prompt_tokens, output), cfg.premium)

    if route == "template":
        # LLM을 안 불렀으니 토큰을 추정한다. 입력은 글자 수 × 0.786, 출력은 고성능 평균 175토큰
        return request_cost(LLMUsage(round(len(prompt_text) * 0.786), 175), cfg.premium)

    # route == "block": 차단한 요청은 절감액에 넣지 않는다
    return 0.0
