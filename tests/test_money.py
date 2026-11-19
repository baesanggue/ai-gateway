"""비용을 원화로 보여 주기 (#132). 설문(D31)에서 "$0.0036" 같은 달러 표시가 3.0점으로 가장 낮았다."""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from admin.flow import build_steps  # noqa: E402
from admin.money import krw, rate_line, saving_sentence  # noqa: E402
from gateway.config import DisplayConfig, load_settings  # noqa: E402

RATE = 1340.0


@pytest.mark.parametrize("usd, expected", [
    (0.0, "0원"),
    (None, "0원"),
    (1.0, "약 1,340원"),          # 10원 이상은 정수, 천 단위 쉼표
    (0.0036, "약 4.8원"),         # 1원 이상은 소수 한 자리
    (0.0001, "약 0.13원"),        # 요청 한 건의 비용: 1원도 안 되지만 0원으로 뭉개지지 않는다
    (0.000001, "0.01원 미만"),
])
def test_krw_keeps_small_amounts_visible(usd, expected):
    assert krw(usd, RATE) == expected


def test_saving_sentence_reads_as_one_sentence():
    s = saving_sentence(cost=0.0001, base=0.0036, n=2, rate=RATE)
    assert s == ("모든 요청을 비싼 고성능 모델로 보냈다면 약 4.8원, 가림게이트로는 약 0.13원이 들어 97%를 아꼈습니다. "
                 "같은 종류의 요청 1,000건이면 약 2,345원을 아낍니다.")   # (0.0035 / 2) × 1,000 × 1,340


def test_saving_sentence_before_any_request_and_without_savings():
    assert "비교해 보여 줍니다" in saving_sentence(0.0, 0.0, 0, RATE)
    assert "아낀 비용은 없습니다" in saving_sentence(0.002, 0.002, 1, RATE)    # 모두 고성능이 필요했던 경우


def test_all_templates_save_everything():
    assert "가림게이트로는 0원이 들어 100%를 아꼈습니다" in saving_sentence(0.0, 0.002, 1, RATE)


def test_rate_line_shows_rate_and_basis():
    assert rate_line(RATE, "2026년 11월 기준") == "1달러 = 1,340원(2026년 11월 기준)으로 계산했습니다."


def test_rate_comes_from_config_and_must_be_positive(tmp_path):
    cfg = load_settings(ROOT / "config.yaml", env_file=tmp_path / "없는.env").config
    assert cfg.display.krw_per_usd == 1340 and "2026년 11월" in cfg.display.rate_note
    with pytest.raises(ValidationError):
        DisplayConfig(krw_per_usd=0)


def test_process_step_shows_cost_in_won():
    row = {"route": "cheap", "reasons": ["simple"], "pii_counts": {}, "judgments": {}, "text_masked": "요청",
           "model": "gpt-6-luna", "cost_usd": 0.0001, "baseline_cost_usd": 0.002, "prompt_tokens": 50,
           "completion_tokens": 80, "total_latency_ms": 2400.0, "unrestored_tokens": 0}
    details = dict(build_steps(row, {}, rate=RATE)[3].details)
    assert details["비용"] == "약 0.13원 (전부 고성능이었다면 약 2.7원)"
    assert dict(build_steps(row, {})[3].details)["비용"] == details["비용"]    # 환율을 안 주면 설정 기본값
