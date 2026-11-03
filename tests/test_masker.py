"""규칙 가명화 검사 (#44): 사용자 사전 + 정규식 → [종류N] 토큰, 같은 값 = 같은 토큰, 메시지 전체."""
import logging

import pytest

from gateway.config import MaskingConfig
from gateway.masking.dictionary import DictionaryMissing, Entry, load, parse
from gateway.masking.masker import RuleMasker
from tests.fakes import DEMO_DICTIONARY

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


def test_missing_dictionary_refuses_to_start(tmp_path):
    """사전이 없으면 시연용 사전으로 조용히 넘어가지 않고 시작하지 않는다 (D21). 복사 방법을 알려 준다."""
    with pytest.raises(DictionaryMissing) as e:
        load(MaskingConfig(user_dictionary_path=str(tmp_path / "없음.txt")))
    assert "copy" in str(e.value) and "user_dictionary.example.txt" in str(e.value)


def test_copied_demo_dictionary_is_flagged_with_warning(tmp_path, caplog):
    real = tmp_path / "user_dictionary.txt"
    real.write_text(DEMO_DICTIONARY.read_text(encoding="utf-8"), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        entries, _, is_example = load(MaskingConfig(user_dictionary_path=str(real)))
    assert is_example and Entry("고객", "김민수") in entries
    assert "시연용 가상 사전으로 동작 중" in caplog.text
    assert RuleMasker.from_config(MaskingConfig(user_dictionary_path=str(real))).dictionary_source == "example"


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


@pytest.mark.parametrize("written", ["[고객 1]", "【고객1】", "고객1님"])
def test_rewritten_token_like_text_is_not_reused(written):
    """#45: 복원이 바꿔 쓴 모양도 되돌리므로, 사용자가 그 모양을 직접 써도 그 번호는 건너뛴다."""
    r = mask(f"{written} 양식에 김민수 고객 이름을 넣어 줘")
    assert "[고객1]" not in r.mapping and r.mapping["[고객2]"] == "김민수"
    assert RuleMasker(ENTRIES).restore(f"{written} 양식, [고객2]", r.mapping).text == f"{written} 양식, 김민수"


@pytest.mark.parametrize("text", ["2026-11-03 회의록 정리해 줘", "주문번호 2026101100123 취소해 주세요", "배송 언제 와요?"])
def test_non_pii_is_left_alone(text):
    r = mask(text)
    assert r.messages[0]["content"] == text and not r.has_pii


def test_restore_round_trip():
    r = mask("김민수 고객(010-1234-5678)에게 사과 메일 써줘")
    answer = "[고객1] 고객님, [전화번호1]로 다시 연락드리겠습니다. [고객9]는 없는 토큰입니다."
    restored = RuleMasker(ENTRIES).restore(answer, r.mapping)
    assert restored.text == "김민수 고객님, 010-1234-5678로 다시 연락드리겠습니다. [고객9]는 없는 토큰입니다."
    assert restored.unrestored == 1


# ── 표기 차이로 피해 가지 못하게 (#44 보안 검토) ──────────────

def test_decomposed_hangul_name_is_masked():
    import unicodedata
    nfd = unicodedata.normalize("NFD", "김민수 고객에게 메일 써 줘")      # 눈으로는 같지만 자모가 나뉜 표기
    assert nfd != "김민수 고객에게 메일 써 줘"
    assert mask(nfd).messages[0]["content"] == "[고객1] 고객에게 메일 써 줘"


def test_dictionary_ignores_latin_case_and_keeps_one_token():
    r = mask("x-프로젝트 일정과 X-프로젝트 예산")
    assert r.messages[0]["content"] == "[사내1] 일정과 [사내1] 예산"
    assert r.mapping == {"[사내1]": "x-프로젝트"}


@pytest.mark.parametrize("text", ["010–1234–5678로 연락", "010—1234—5678로 연락", "０１０-１２３４-５６７８로 연락",
                                  "010－1234－5678로 연락"], ids=["en-dash", "em-dash", "full-width", "full-width-hyphen"])
def test_dash_and_full_width_variants_are_masked_as_phone(text):
    r = mask(text)
    assert r.messages[0]["content"] == "[전화번호1]로 연락"
    assert r.mapping["[전화번호1]"] == "010-1234-5678"          # 표기를 맞춘 값으로 복원된다


def test_normalization_keeps_korean_jamo():
    """NFKC를 쓰면 ㅋㅋ·ㅠㅠ가 다른 문자(조합용 자모)로 바뀐다. NFC + 전각·대시만 맞추므로 그대로다."""
    assert mask("ㅋㅋ 배송 언제 와요 ㅠㅠ").messages[0]["content"] == "ㅋㅋ 배송 언제 와요 ㅠㅠ"


@pytest.mark.parametrize("text, expected", [
    ("김​민수 고객", "[고객1] 고객"),                 # 폭 0 공백
    ("김­민수 고객", "[고객1] 고객"),                 # 소프트 하이픈
    ("김️민수 고객", "[고객1] 고객"),                 # 이체 선택자
    ("﻿김민수 고객", "[고객1] 고객"),                 # BOM
    ("010​-1234-5678", "[전화번호1]"),
    ("010　1234　5678", "[전화번호1]"),           # 전각 공백
    ("010‐1234‐5678", "[전화번호1]"),           # 유니코드 하이픈 (Pd)
    ("010−1234−5678", "[전화번호1]"),           # 빼기 기호
], ids=["zwsp", "soft-hyphen", "variation-selector", "bom", "zwsp-phone", "ideographic-space", "unicode-hyphen", "minus"])
def test_invisible_and_variant_characters_do_not_bypass(text, expected):
    r = mask(text)
    assert r.messages[0]["content"] == expected and r.unmasked == []


def test_compat_only_forms_are_reported_as_unmasked():
    """동그라미 숫자처럼 NFKC로만 풀리는 표기는 가리지 못한다. 대신 unmasked로 알려 파이프라인이 막는다."""
    r = mask("⓪①⓪-①②③④-⑤⑥⑦⑧로 연락 주세요")
    assert r.unmasked == ["전화번호"]


def test_kind_lookup_miss_still_masks():
    """정규식 대소문자 무시와 casefold()가 어긋나도 오류(500)가 나지 않고 값은 가려진다."""
    m = RuleMasker([Entry("사내", "X-프로젝트")])
    m.kinds.clear()                                         # 조회가 빗나가는 상황을 흉내 낸다
    r = m.mask([{"role": "user", "content": "x-프로젝트 일정"}])
    assert r.messages[0]["content"] == "[고객1] 일정"


@pytest.mark.parametrize("text, expected", [
    ("김ㅤ민수 고객", "[고객1] 고객"),                 # 한글 채움 문자 "ㅤ" (빈 닉네임에 흔히 쓰임)
    ("김ᅟ민수 고객", "[고객1] 고객"),                 # 초성 채움 문자
    ("김͏민수 고객", "[고객1] 고객"),                 # 결합 문자 연결자
    ("김⠀민수 고객", "[고객1] 고객"),                 # 점자 빈칸
    ("٠١٠-١٢٣٤-٥٦٧٨", "[전화번호1]"),   # 아랍-인도 숫자
    ("०१०-१२३४-५६७८", "[전화번호1]"),   # 데바나가리 숫자
], ids=["hangul-filler", "choseong-filler", "cgj", "braille-blank", "arabic-indic-digits", "devanagari-digits"])
def test_default_ignorables_and_other_digits_do_not_bypass(text, expected):
    r = mask(text)
    assert r.messages[0]["content"] == expected and r.unmasked == []


def test_dictionary_values_in_compat_only_forms_are_reported():
    """수학 글꼴 영문처럼 NFKC로만 풀리는 사전 값은 가리지 못한다. 마지막 검사가 사전 값도 찾아 unmasked로 알린다."""
    assert mask("\U0001d417-프로젝트 일정 공유해 줘").unmasked == ["사내"]
