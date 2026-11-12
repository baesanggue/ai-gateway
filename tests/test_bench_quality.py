"""답변 품질 벤치마크(#62)의 도구: 순서를 섞은 채점을 원래 쪽으로 되돌리기, 요약·직접 확인 파일. 네트워크 없이."""
import random
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import run_quality as rq  # noqa: E402

PRICE = SimpleNamespace(price_per_1m_input=1.0, price_per_1m_output=1.0)
USAGE = SimpleNamespace(prompt_tokens=10, completion_tokens=10)


class FakeJudge:
    """채점자: 보여 준 두 답 중 '좋은 답'이 든 쪽을 고른다. 답 앞뒤에 설명이 붙어도 JSON을 찾아야 한다."""
    cfg = SimpleNamespace(premium=PRICE)

    def complete(self, tier, msgs):
        body = msgs[-1]["content"]
        first = body.split("[답변 1]")[1].split("[답변 2]")[0]
        better = "1" if "좋은 답" in first else "2"
        good, bad = {"fulfil": 5, "accuracy": 5, "fluency": 5}, {"fulfil": 2, "accuracy": 2, "fluency": 2}
        s1, s2 = (good, bad) if better == "1" else (bad, good)
        text = f'평가 결과입니다.\n{{"score_1": {s1}, "score_2": {s2}, "better": "{better}", "reason": "r"}}'.replace("'", '"')
        return SimpleNamespace(text=text, usage=USAGE)


def test_blind_judge_maps_shuffled_order_back_to_names():
    """어느 순서로 보여 주든, 좋은 답을 낸 쪽의 이름과 점수가 돌아와야 한다 (뒤집히면 결론이 반대가 된다)."""
    llm, rnd = FakeJudge(), random.Random(0)
    for _ in range(20):                                     # 두 순서가 모두 나오도록 여러 번
        sx, sy, pick, _, cost = rq.blind_judge(llm, rnd, "요청", "좋은 답", "나쁜 답", ("original", "masked"))
        assert pick == "original" and sx["fulfil"] == 5 and sy["fulfil"] == 2 and cost > 0
        sx, sy, pick, _, _ = rq.blind_judge(llm, rnd, "요청", "나쁜 답", "좋은 답", ("original", "masked"))
        assert pick == "masked" and sx["fulfil"] == 2 and sy["fulfil"] == 5


def _row(id_, better, control="same"):
    return {"id": id_, "tier": "cheap", "request": "요청", "masked_request": "[고객1] 요청", "answer_original": "A",
            "answer_masked_raw": "[고객1] B", "answer_restored": "B", "unrestored": 0, "tokens_left": 0,
            **{f"orig_{c}": 5 for c in rq.CRITERIA}, **{f"mask_{c}": 4 for c in rq.CRITERIA},
            "better": better, "reason": f"{id_} 이유", "control": control, "error": ""}


def test_summary_counts_preferences_and_control(tmp_path):
    rows = [_row("T1", "same"), _row("T2", "original", "original2"), _row("T3", "masked"),
            {"id": "T4", "error": "LLMError: x"}]
    rq.write_summary(tmp_path, rows, 0.01, 60)
    text = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "원문이 낫다 1 · 같다 1 · 가린 쪽이 낫다 1" in text
    assert "67%** (2/3)" in text                      # 같거나 가린 쪽이 나은 비율, 실패는 빼고
    assert "원문끼리도 33%는 한쪽을 고른다" in text
    assert "| 요청 충족 | 5.00 | 4.00 | -1.00 |" in text
    assert "T2: T2 이유" in text and "T4: LLMError" in text


def test_review_puts_worse_masked_answers_first(tmp_path):
    rows = [_row(f"T{i}", "same") for i in range(12)] + [_row("T99", "original")]
    rq.write_review(tmp_path, rows, random.Random(0))
    text = (tmp_path / "review.md").read_text(encoding="utf-8")
    assert text.count("## T") == 10 and text.index("## T99") == text.index("## T")   # 원문이 나았던 것이 맨 앞
