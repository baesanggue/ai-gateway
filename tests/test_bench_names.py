"""이름 찾기 측정기(#53)의 채점: 찾은 이름, 일부만 찾음, 잘못 가림, 기준값 고르기."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))

import run_names as rn  # noqa: E402


def row(names, options, probs, set_="testset"):
    return {"set": set_, "id": "x", "names": "|".join(names), "options": json.dumps(options, ensure_ascii=False),
            "probs": json.dumps(probs, ensure_ascii=False), "questions": str(len(probs)), "latency_ms": "1",
            "error": ""}


def test_found_names_longest_option_over_threshold():
    r = row(["박지은"], [["박지은", "박지"], ["고객"]], {"박지은": 0.2, "박지": 0.6, "고객": 0.7})
    assert rn.found_names(r, 0.5) == ["박지", "고객"]


def test_score_counts_hit_partial_and_wrong():
    rows = [row(["박지은"], [["박지은", "박지"], ["고객"]], {"박지은": 0.2, "박지": 0.6, "고객": 0.7}),
            row(["양미래"], [["양미래"]], {"양미래": 0.9}),
            row([], [["어떻게"]], {"어떻게": 0.1})]
    s = rn.score(rows, 0.5)
    assert (s["names"], s["hit"], s["partial"], s["wrong"]) == (2, 1, 1, 1)    # "박지"는 일부만, "고객"은 잘못 가림
    assert s["wrong_ids"] == [("x", ["고객"])]


def test_choose_threshold_keeps_recall_target():
    rows = [row([f"김가{i}"], [[f"김가{i}"]], {f"김가{i}": p}) for i, p in enumerate([0.9, 0.8, 0.7, 0.6, 0.5,
                                                                                     0.4, 0.3, 0.2, 0.15, 0.1,
                                                                                     0.09, 0.08, 0.07, 0.06, 0.05,
                                                                                     0.04, 0.03, 0.02, 0.01, 0.005])]
    assert rn.choose_threshold(rows) == 0.01                         # 20개 중 19개(95%)를 지키는 가장 높은 값


def test_rare_names_cover_two_syllable_surnames_and_unlisted_ones():
    assert len(rn.RARE) == len(set(rn.RARE)) == 32
    assert any(n.startswith(("남궁", "황보", "제갈", "선우", "독고")) for n in rn.RARE)
    assert {"묵하진", "섭지우"} <= set(rn.RARE)                       # 성씨 목록 밖 → 놓치는 것이 정상
