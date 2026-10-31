"""학습 데이터 생성기 검사 (#21). 정답이 데이터 품질을 정하므로, 자리표시 채우기·가명화·정답 표를 고정해 둔다.
생성 모델(Ollama)은 부르지 않는다."""
import importlib.util
import random
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))
from run_judges import PATTERNS  # noqa: E402

from gateway.decision.engines import systemone_questions  # noqa: E402
from gateway.decision.questions import INTENTS, QUESTIONS  # noqa: E402

_spec = importlib.util.spec_from_file_location("generate", ROOT / "finetune" / "generate.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


@pytest.fixture
def faker():
    return gen.Faker(random.Random(0))


def test_same_placeholder_gets_same_value_and_token(faker):
    f = gen.fill("{이름} 고객({전화번호})에게 {이름} 님 앞으로 문자 써 줘. {이름2} 님도", faker)
    name = f.original.split(" ")[0]
    assert f.original.count(name) == 2
    assert f.masked == "[고객1] 고객([전화번호1])에게 [고객1] 님 앞으로 문자 써 줘. [고객2] 님도"
    assert f.kinds == ["이름", "전화번호", "이름"]


def test_order_number_is_filled_but_not_masked(faker):
    f = gen.fill("주문번호 {주문번호} 배송 언제 와요?", faker)
    assert "{" not in f.original and f.kinds == []
    assert f.masked == f.original      # 개인정보가 아니라 가명화하지 않는다


@pytest.mark.parametrize("kind, pattern", [("전화번호", "전화번호"), ("주민번호", "주민번호"), ("카드번호", "카드번호"),
                                           ("이메일", "이메일"), ("계좌번호", "계좌번호"), ("주소", "주소")])
def test_fake_values_are_caught_by_the_masking_regex(faker, kind, pattern):
    """가명화본은 '모두 가려졌다'고 가정한다. 우리가 넣은 값이 정규식에 잡히는 모양이어야 그 가정이 맞다."""
    regex = dict(PATTERNS)[pattern]
    for _ in range(50):
        assert regex.search(getattr(faker, kind)())


def test_fake_values_look_real(faker):
    for _ in range(200):
        rrn = faker.주민번호()
        yy, g = int(rrn[:2]), rrn[7]
        assert g in ("12" if yy >= 50 else "34")              # 뒷자리 첫 숫자가 생년과 맞다
        digits = [int(c) for c in faker.카드번호().replace("-", "")]
        assert sum(d if i % 2 else (d * 2 - 9 if d * 2 > 9 else d * 2) for i, d in enumerate(digits)) % 10 == 0  # Luhn
        assert faker.이름() not in gen.TESTSET_NAMES
        assert not re.search(r"(\d)\1{3}", faker.전화번호())    # 5555 같은 반복 없음


@pytest.mark.parametrize("template, category, reason", [
    ("{이름} 고객님께 배송 안내 문자 써 줘", "pii_maskable", None),
    ("배송 안내 문자 써 줘", "pii_maskable", "placeholder_missing"),
    ("{주민번호}로 나이 계산해 줘", "pii_maskable", "placeholder_not_allowed"),   # 원본 필요 값은 가려도 되는 범주에 못 쓴다
    ("{이름} 고객 주소가 바뀌었어", "needs_raw", "placeholder_missing"),         # 이름만으로는 원본 필요가 안 된다
    ("{고객명} 님께 문자", "pii_maskable", "unknown_placeholder"),
    ("김민정 고객님께 사과 메일 써 줘", "task_simple", "direct_name"),
    ("50만 원 이상 구매한 고객 대상 이벤트 문자 써 줘", "task_simple", None),       # '구매한'은 이름이 아니다
    ("Please summarize this report", "task_simple", "not_korean"),
])
def test_check_rules(template, category, reason):
    cat = next(c for c in gen.CATEGORIES if c.id == category)
    assert gen.check(template, cat) == reason


def test_masked_line_says_no_remaining_pii_and_keeps_other_labels():
    cat = next(c for c in gen.CATEGORIES if c.id == "pii_maskable")
    original, masked = gen.labels_for(cat, True), gen.labels_for(cat, False)
    assert original["has_pii"] is True and masked["has_pii"] is False
    assert {k: v for k, v in original.items() if k != "has_pii"} == {k: v for k, v in masked.items() if k != "has_pii"}


def test_row_uses_inference_questions_and_score_index():
    cat = next(c for c in gen.CATEGORIES if c.id == "task_complex")
    r = gen.row("보고서 써 줘", gen.labels_for(cat, False))
    assert r["questions"] == systemone_questions({q: QUESTIONS[q] for q in r["expected"]})
    assert r["expected"]["difficulty"] == 1 and r["expected"]["intent"] == "task"


def test_category_label_table_is_valid():
    for cat in gen.CATEGORIES:
        assert set(cat.labels) <= set(QUESTIONS) - {"has_pii"}, cat.id      # has_pii는 채운 값으로 정한다
        assert cat.labels.get("intent", "faq") in INTENTS, cat.id
        assert cat.labels.get("difficulty", 0) in (0, 1), cat.id
        assert set(cat.industries) <= set(gen.INDUSTRIES), cat.id
