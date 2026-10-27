"""bench/analyze_judges.py의 기준값 선택 규칙 검사."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))
from analyze_judges import auc, choose_threshold  # noqa: E402


def test_threshold_separates_when_possible():
    # 양성은 모두 0.8 이상, 음성은 모두 0.3 이하 → 둘을 완벽히 가르는 기준값
    t = choose_threshold([0.8, 0.9, 0.95], [0.1, 0.2, 0.3])
    assert 0.3 < t <= 0.8


def test_tie_prefers_lower_threshold():
    # 기준값 0.4: 양성 3/3·오탐 1/3, 0.6: 양성 2/3·오탐 0/3 → J가 같다(0.67)
    # 같으면 더 낮은 0.4를 고른다 (놓치는 것보다 오탐이 낫다)
    assert choose_threshold([0.4, 0.6, 0.9], [0.1, 0.2, 0.4]) == 0.4


def test_threshold_not_stuck_at_half_for_shifted_scores():
    # d3:edge처럼 전부 0.5 아래에 몰려 있어도 순서만 맞으면 잡아낸다
    t = choose_threshold([0.30, 0.35, 0.40], [0.05, 0.10, 0.12])
    assert t <= 0.30


def test_no_positive_falls_back_to_default():
    assert choose_threshold([], [0.1, 0.2]) == 0.5


def test_auc_bounds():
    assert auc([0.9], [0.1]) == 1.0
    assert auc([0.1], [0.9]) == 0.0
    assert auc([0.5], [0.5]) == 0.5
    assert auc([], [0.1]) is None
