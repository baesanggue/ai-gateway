"""파인튜닝 비교 측정기(#22)의 집계. 기준값을 시범 데이터에서 고르는 규칙과 테스트셋 지표 계산을 고정한다 (모델·서버 없이)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import run_finetuned as rf  # noqa: E402


def pilot_row(expected, **probs):
    full = {"has_pii": {"yes": 0.1}, "is_confidential": {"yes": 0.1}, "needs_raw_value": {"yes": 0.1},
            "is_risky": {"yes": 0.1}, "difficulty": {"simple": 0.9, "complex": 0.1},
            "intent": {"faq": 0.1, "service": 0.1, "task": 0.7, "off_topic": 0.1}}
    full.update(probs)
    return {"expected": expected, "probs": full}


def test_threshold_is_highest_value_that_keeps_the_recall_target():
    """위험 목표 재현율 95%: '예' 20개 중 19개 이상을 잡는 가장 높은 기준값."""
    pilot = [pilot_row({"is_risky": True}, is_risky={"yes": p}) for p in [0.9] * 18 + [0.4, 0.2]]
    pilot += [pilot_row({"is_risky": False}, is_risky={"yes": 0.3})]
    assert rf.choose_thresholds(pilot)["is_risky"] == 0.4          # 0.4면 19/20 = 95%, 0.9면 18/20


def test_difficulty_threshold_maximizes_pilot_accuracy():
    pilot = [pilot_row({"difficulty": 1}, difficulty={"simple": 0.3, "complex": 0.7}) for _ in range(3)]
    pilot += [pilot_row({"difficulty": 0}, difficulty={"simple": 0.8, "complex": 0.2}) for _ in range(3)]
    th = rf.choose_thresholds(pilot)["difficulty_complex"]
    assert 0.2 < th <= 0.7


def test_missing_labels_fall_back_to_half():
    assert rf.choose_thresholds([pilot_row({})]) == {"is_confidential": 0.5, "needs_raw_value": 0.5,
                                                     "is_risky": 0.5, "difficulty_complex": 0.5}


def test_testset_metrics_count_unasked_needs_raw_as_no_and_group_intent():
    base = {"error": "", "expected_route": "cheap", "is_confidential_label": "0", "needs_raw_label": "0",
            "is_risky_label": "0", "difficulty_truth": "simple", "intent_truth": "work", "p_confidential": "0.1",
            "p_needs_raw": "", "p_risky": "0.1", "p_complex": "0.1",
            "intent_probs": json.dumps({"faq": 0.1, "service": 0.6, "task": 0.2, "off_topic": 0.1})}
    rows = [dict(base, id="r1"),
            dict(base, id="r2", needs_raw_label="1", p_needs_raw="0.9", asked_needs_raw="1"),
            dict(base, id="r3", needs_raw_label="1", p_needs_raw=""),               # 묻지 않음 → 아니오 (놓침)
            dict(base, id="r4", difficulty_truth="complex", p_complex="0.8", intent_truth="faq",
                 intent_probs=json.dumps({"faq": 0.7, "service": 0.1, "task": 0.1, "off_topic": 0.1}))]
    m = rf.metrics(rows, {"is_confidential": 0.5, "needs_raw_value": 0.5, "is_risky": 0.5, "difficulty_complex": 0.5})
    assert m["needs_raw_value"].startswith("50% (1/2)")
    assert m["difficulty"] == "100% (4/4)" and m["intent"] == "100% (4/4)"   # service → work 묶음
