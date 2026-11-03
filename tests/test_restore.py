"""응답 복원과 조사 교정 (#45). 이름·번호는 전부 가상 값."""
import pytest

from gateway.masking.restore import final_consonant, fix_josa, restore

MAPPING = {"[고객1]": "김민수", "[고객2]": "박지민", "[고객3]": "이하늘", "[전화번호1]": "010-1234-5678",
           "[주소1]": "서울시 강남구 테헤란로 113", "[주소2]": "서울시 중구 세종대로 110", "[이메일1]": "minsu@example.com"}


def text(s):
    return restore(s, MAPPING).text


# ── 받침 ────────────────────────────────────────────

@pytest.mark.parametrize("value, final", [
    ("김민수", ""), ("박지민", "ㄴ"), ("이하늘", "ㄹ"), ("홍길동", "ㅇ"),
    ("…113", "ㅁ"), ("…110", "ㅂ"), ("…100", "ㅂ"), ("…5678", "ㄹ"), ("…1234", ""), ("…26", "ㄱ"),
    ("세종대로 110)", "ㅂ"), ("minsu@example.com", None), ("Project-X", None), ("", None),
])
def test_final_consonant(value, final):
    assert final_consonant(value) == final


# ── 조사 교정: 받침 있음·없음·ㄹ받침·숫자 ───────────────

@pytest.mark.parametrize("answer, expected", [
    ("[고객1]은 [고객2]는", "김민수는 박지민은"),          # 은/는
    ("[고객1]이 [고객2]가", "김민수가 박지민이"),          # 이/가
    ("[고객1]을 [고객2]를", "김민수를 박지민을"),          # 을/를
    ("[고객1]과 [고객2]와", "김민수와 박지민과"),          # 과/와
    ("[고객1]으로 [고객2]로", "김민수로 박지민으로"),       # 으로/로
    ("[고객3]으로 보내", "이하늘로 보내"),                 # ㄹ받침은 "로"
    ("[고객1]이나 [고객2]나", "김민수나 박지민이나"),
    ("[고객1]이랑 [고객2]랑", "김민수랑 박지민이랑"),
    ("[고객1]이에요. [고객2]예요.", "김민수예요. 박지민이에요."),
    ("[고객1]이라고 [고객2]라고", "김민수라고 박지민이라고"),
])
def test_josa_follows_restored_name(answer, expected):
    assert text(answer) == expected


@pytest.mark.parametrize("answer, expected", [
    ("[전화번호1]으로 연락드릴게요", "010-1234-5678로 연락드릴게요"),           # 8(팔): ㄹ받침
    ("[주소1]를 확인해 주세요", "서울시 강남구 테헤란로 113을 확인해 주세요"),  # 3(삼)
    ("[주소2]로 보냅니다", "서울시 중구 세종대로 110으로 보냅니다"),           # 110(백십)
])
def test_josa_after_numbers_follows_reading(answer, expected):
    assert text(answer) == expected


def test_josa_after_latin_value_is_left_as_written():
    """이메일처럼 영문으로 끝나면 읽는 법이 하나가 아니라 LLM이 쓴 조사를 그대로 둔다."""
    assert text("[이메일1]로 보내고 [이메일1]을 저장") == "minsu@example.com로 보내고 minsu@example.com을 저장"


@pytest.mark.parametrize("answer, expected", [
    ("[고객1]으로는 [고객2]과도 [고객1]이랑만", "김민수로는 박지민과도 김민수랑만"),   # 조사 뒤에 붙는 말
    ("[고객1]은요?", "김민수는요?"),
    ("[고객2]는, [고객1]은!", "박지민은, 김민수는!"),
])
def test_josa_followed_by_suffix_or_punctuation(answer, expected):
    assert text(answer) == expected


@pytest.mark.parametrize("answer, expected", [
    ("[고객2]이다.", "박지민이다."),       # 서술격 조사 "이다"는 이/가가 아니다
    ("[고객1]님께", "김민수님께"),
    ("[고객1] 고객님", "김민수 고객님"),
    ("[고객1]가요", "김민수가요"),          # 고칠 필요 없는 형태는 그대로
])
def test_words_that_are_not_josa_are_untouched(answer, expected):
    assert text(answer) == expected


def test_fix_josa_unknown_ending_keeps_josa():
    assert fix_josa("abc", "은") == "은" and fix_josa("김민수", "은") == "는"


# ── LLM이 바꿔 쓴 토큰 ─────────────────────────────────

@pytest.mark.parametrize("answer, expected", [
    ("[고객 1]님", "김민수님"),
    ("[ 고객1 ]님", "김민수님"),
    ("【고객1】님, ［전화번호1］로", "김민수님, 010-1234-5678로"),
    ("〔고객2〕은", "박지민은"),
    ("고객1님께 연락", "김민수님께 연락"),
    ("고객2는 VIP", "박지민은 VIP"),
    ("고객 1, 고객2.", "김민수, 박지민."),
])
def test_rewritten_tokens_are_restored(answer, expected):
    assert text(answer) == expected


@pytest.mark.parametrize("answer", [
    "고객1명이 문의했습니다",        # 숫자 세는 말: 토큰이 아니다
    "VIP고객1 등급",                 # 다른 낱말 안
    "고객12에게",                    # 대응표에 없는 번호
    "[고객12]에게",
])
def test_lookalikes_are_not_restored(answer):
    assert text(answer) == answer


# ── 복원 못 한 토큰 수 (로그용) ─────────────────────────

def test_unrestored_counts_unknown_tokens_only():
    r = restore("[고객1]님, [고객9]와 [주소3] 그리고 [참고1]", MAPPING, kinds={"고객", "주소"})
    assert r.text == "김민수님, [고객9]와 [주소3] 그리고 [참고1]"
    assert r.unrestored == 2            # [참고1]은 가명화 종류가 아니라 세지 않는다


def test_everything_restored_counts_zero():
    assert restore("[고객1]님 [전화번호1]", MAPPING).unrestored == 0


def test_empty_mapping_returns_text_and_counts_known_kinds():
    r = restore("[고객1]님", {}, kinds={"고객"})
    assert (r.text, r.unrestored) == ("[고객1]님", 1)


def test_value_that_looks_like_token_is_not_restored_again():
    """복원한 값이 다시 토큰처럼 보여도 한 번만 바꾼다."""
    assert restore("[고객1]님", {"[고객1]": "[고객2]", "[고객2]": "박지민"}).text == "[고객2]님"


# ── 시연 4번 ────────────────────────────────────────

def test_scenario_4_reads_naturally():
    answer = ("[고객1] 고객님, 주문하신 상품의 배송이 늦어져 죄송합니다. [고객1]님께서 남겨 주신 [전화번호1]으로 "
              "출발 안내를 드리겠습니다.")
    assert text(answer) == ("김민수 고객님, 주문하신 상품의 배송이 늦어져 죄송합니다. 김민수님께서 남겨 주신 "
                            "010-1234-5678로 출발 안내를 드리겠습니다.")
