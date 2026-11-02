"""규칙 가명화 검사 (#44): 사용자 사전 + 정규식 → [종류N] 토큰, 같은 값 = 같은 토큰, 메시지 전체."""
import logging

import pytest

from gateway.config import MaskingConfig
from gateway.masking.dictionary import Entry, load, parse
from gateway.masking.masker import RuleMasker

ENTRIES = [Entry("고객", "김민수"), Entry("고객", "김민수진"), Entry("직원", "최유진"), Entry("사내", "X-프로젝트")]


def mask(*contents, roles=None):
    roles = roles or ["user"] * len(contents)
    return RuleMasker(ENTRIES).mask([{"role": r, "content": c} for r, c in zip(roles, contents)])


# ── 사전 ─────────────────────────────────────────────────────

def test_dictionary_format():
    text = "# 주석\n\n고객,김민수\n직원 , 최유진\n박지훈\n고객,김민수\n"
    assert parse(text) == [Entry("고객", "김민수"), Entry("직원", "최유진"), Entry("고객", "박지훈")]


@pytest.mark.parametrize("text, message", [("고객1,김민수", "1번째 줄: 종류"), ("고객,김", "1번째 줄: 값이 너무 짧다"),
                                           ("[고객],김민수", "1번째 줄: 종류")])
def test_bad_dictionary_lines_are_reported_without_the_value(text, message):
    with pytest.raises(ValueError) as e:
        parse(text)
    assert message in str(e.value) and "김" not in str(e.value)     # 실제 이름은 오류 메시지에 남기지 않는다


def test_falls_back_to_demo_dictionary_with_warning(tmp_path, caplog):
    cfg = MaskingConfig(user_dictionary_path=str(tmp_path / "없음.txt"))
    with caplog.at_level(logging.WARNING):
        entries, path, is_example = load(cfg)
    assert is_example and Entry("고객", "김민수") in entries and path.name == "user_dictionary.example.txt"
    assert "시연용 가상 사전" in caplog.text


def test_real_dictionary_is_used_when_present(tmp_path):
    real = tmp_path / "user_dictionary.txt"
    real.write_text("고객,홍길동\n", encoding="utf-8")
    entries, path, is_example = load(MaskingConfig(user_dictionary_path=str(real)))
    assert (entries, path, is_example) == ([Entry("고객", "홍길동")], real, False)


# ── 가명화 ───────────────────────────────────────────────────

def test_demo_scenario_4():
    r = mask("김민수 고객(010-1234-5678)에게 배송 지연 사과 메일 써줘")
    assert r.messages[0]["content"] == "[고객1] 고객([전화번호1])에게 배송 지연 사과 메일 써줘"
    assert r.mapping == {"[고객1]": "김민수", "[전화번호1]": "010-1234-5678"}
    assert r.counts == {"고객": 1, "전화번호": 1} and r.has_pii


def test_same_value_same_token_across_messages_and_roles():
    """메시지 전체(#37): system·이전 답변·도구 결과까지. 같은 값은 어느 메시지에 있든 같은 토큰."""
    r = mask("김민수 고객 응대 규칙", "김민수님께 010-1234-5678로 연락드렸습니다.", "고객 정보: 김민수, 010-1234-5678",
             "김민수 고객에게 다시 연락해 줘", roles=["system", "assistant", "tool", "user"])
    assert [m["content"] for m in r.messages] == [
        "[고객1] 고객 응대 규칙", "[고객1]님께 [전화번호1]로 연락드렸습니다.", "고객 정보: [고객1], [전화번호1]",
        "[고객1] 고객에게 다시 연락해 줘"]
    assert [m["role"] for m in r.messages] == ["system", "assistant", "tool", "user"]
    assert r.counts == {"고객": 1, "전화번호": 1}


def test_kinds_longest_value_first_and_numbering():
    r = mask("김민수진 고객과 김민수 고객, 담당 최유진, X-프로젝트 일정. 연락처 010-1111-2222, 010-3333-4444")
    assert r.messages[0]["content"] == ("[고객1] 고객과 [고객2] 고객, 담당 [직원1], [사내1] 일정. "
                                        "연락처 [전화번호1], [전화번호2]")
    assert r.mapping["[고객1]"] == "김민수진" and r.mapping["[고객2]"] == "김민수"


def test_existing_token_like_text_is_not_reused():
    """사용자가 [고객1]을 직접 썼으면 그 번호는 건너뛴다. 복원할 때 사용자 글자를 다른 값으로 바꾸지 않게."""
    r = mask("[고객1] 양식에 김민수 고객 이름을 넣어 줘")
    assert r.messages[0]["content"] == "[고객1] 양식에 [고객2] 고객 이름을 넣어 줘"
    assert "[고객1]" not in r.mapping


@pytest.mark.parametrize("text", ["2026-11-03 회의록 정리해 줘", "주문번호 2026101100123 취소해 주세요", "배송 언제 와요?"])
def test_non_pii_is_left_alone(text):
    r = mask(text)
    assert r.messages[0]["content"] == text and not r.has_pii


def test_restore_round_trip():
    r = mask("김민수 고객(010-1234-5678)에게 사과 메일 써줘")
    answer = "[고객1] 고객님, [전화번호1]로 다시 연락드리겠습니다. [고객9]는 없는 토큰입니다."
    assert RuleMasker(ENTRIES).restore(answer, r.mapping) == \
        "김민수 고객님, 010-1234-5678로 다시 연락드리겠습니다. [고객9]는 없는 토큰입니다."
