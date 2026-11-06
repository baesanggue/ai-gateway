"""가명화 재측정 실행기(#46)의 계산 부분: 로컬 판단 입력 만들기, 남은 개인정보 정답, 사전 조건, 집계."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))

import run_masked as rm  # noqa: E402

from tests.fakes import test_settings  # noqa: E402

MAPPING = {"[고객1]": "김민수", "[전화번호1]": "010-1234-5678", "[고객10]": "박지훈"}


def test_local_text_variants():
    text = "[고객1]([전화번호1]) 님께 [고객10]"
    assert rm.local_text(text, MAPPING, "token") == text
    assert rm.local_text(text, MAPPING, "word") == "고객(전화번호) 님께 고객"        # [고객10]이 [고객1]로 깨지지 않는다
    assert rm.local_text(text, MAPPING, "drop") == "() 님께"


def test_local_text_keeps_tokens_the_user_wrote():
    """이번 가명화가 만든 토큰만 바꾼다. 입력에 원래 있던 [고객2]는 그대로 둔다."""
    assert rm.local_text("[고객1]과 [고객2]", {"[고객1]": "김민수"}, "word") == "고객과 [고객2]"


def test_residual_names_and_evidence():
    row = {"pii_types": "name|phone|card|address"}
    left, other = rm.residual(row, "박지훈 고객 [계좌번호1], 연락처 010-9876-5432", {"[계좌번호1]": "x"})
    assert left == ["name", "phone", "address"]      # 이름이 남았고, 전화번호 흔적이 있고, 주소는 토큰이 없다
    assert other == ["card"]                          # 카드 토큰은 없지만 번호 흔적도 없다 → 계좌번호로 가려졌다


def test_residual_none_when_fully_masked():
    assert rm.residual({"pii_types": "name|phone"}, "[고객1]([전화번호1])", MAPPING) == ([], [])


def test_dictionary_conditions(tmp_path):
    settings = test_settings(tmp_path)
    text = [{"role": "user", "content": "김민수, 박지훈, 오지현 고객"}]   # 앞의 둘만 시연 사전에 있다
    a = rm.masker_for("A", settings).mask(text).messages[0]["content"]
    b = rm.masker_for("B", settings).mask(text).messages[0]["content"]
    assert not any(n in a for n in rm.NAMES)
    assert "오지현" in b and "김민수" not in b and "박지훈" not in b


def test_auc_and_threshold():
    assert rm.auc([0.9, 0.8], [0.1, 0.2]) == "1.00"
    assert rm.auc([0.1], [0.9]) == "0.00"             # 거꾸로 (가명화된 문장에서 본 현상)
    assert rm.auc([], [0.1]) == "—"
    assert rm.best_threshold([0.9, 0.5, 0.3], [0.4, 0.1]) == "0.300 → 재현율 100% (3/3), 오탐 50% (1/2)"


def test_summarize_local_skips_refused_and_errors():
    base = {"demo": "0", "has_pii": "1", "residual_types": "", "other_kind": "", "is_confidential": "0",
            "p_confidential": "0.01", "confidential_yes": "False", "masked_kinds": "", "local_input": "x",
            "has_pii_yes": "", "error": ""}
    rows = [dict(base, condition=c, variant=v, residual=res, p_has_pii=p)
            for c in rm.CONDITIONS for v in rm.VARIANTS for res, p in (("1", "0.5"), ("0", ""), ("0", "0.01"))]
    rows.append(dict(base, condition="A", variant="token", residual="1", p_has_pii="", error="timeout"))
    text = "\n".join(rm.summarize_local(rows, 0.09, 0.12))
    assert "| A | token | 1 | 100% (1/1) |" in text and "로컬 판단 오류: 1행" in text
