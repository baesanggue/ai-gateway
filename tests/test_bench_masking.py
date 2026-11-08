"""가명화 성능 측정기(#58). 정답을 되찾는 규칙(템플릿 맞대기)과 값별 판정(다 가림·일부만·놓침)을 고정한다."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
import run_masking as rmk  # noqa: E402

from gateway.masking.masker import _kept_chars  # noqa: E402

PLACEHOLDER = re.compile(r"\{(이름|전화번호|주문번호)(\d*)\}")


def test_truth_values_from_template_and_filled_text():
    """같은 자리표시는 같은 값, 번호가 다르면 다른 값. 맞지 않으면 None."""
    template = "{이름} 고객({전화번호})에게 {이름} 님 앞으로, {이름2} 님도. 주문 {주문번호}"
    original = "양미래 고객(010-2345-6789)에게 양미래 님 앞으로, 남궁민 님도. 주문 R1234"
    assert rmk.truth_values(template, original, PLACEHOLDER) == [
        ("이름", "양미래"), ("전화번호", "010-2345-6789"), ("이름", "남궁민"), ("주문번호", "R1234")]
    assert rmk.truth_values(template, "전혀 다른 문장", PLACEHOLDER) is None


def test_status_by_how_many_characters_are_left():
    text = "양유진 님, 양유진 님"
    kept_all = [True] * len(text)
    assert rmk.status(text, kept_all, "양유진") == "missed"
    masked = "[이름1] 님, [이름1] 님"
    assert rmk.status(text, _kept_chars(masked, {"[이름1]": "양유진"}), "양유진") == "masked"
    partial = "양[이름1] 님, 양[이름1] 님"                         # 성이 남았다
    assert rmk.status(text, _kept_chars(partial, {"[이름1]": "유진"}), "양유진") == "partial"
    mixed = "[이름1] 님, 양유진 님"                                # 두 번째 자리를 놓쳤다 → 가장 나쁜 자리
    assert rmk.status(text, _kept_chars(mixed, {"[이름1]": "양유진"}), "양유진") == "missed"
