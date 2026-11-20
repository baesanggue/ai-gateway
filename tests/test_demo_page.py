"""시연 화면의 요청 목록 (#134). 설문(D31): 긴 요청이 "…고객 나이 계"처럼 끊겨 보였고 자른 표시가 없었다."""
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from admin.demo_page import LABEL_CHARS, short  # noqa: E402
from tests.test_first_screen import DEMO, screen_env  # noqa: E402,F401  (같은 화면 준비를 쓴다)

AGE = "이 주민번호 900101-1234567로 고객 나이 계산해줘"         # 설문에 적힌 문장 (예시 ③)


def test_long_request_ends_with_ellipsis_instead_of_a_cut_word():
    assert len(AGE) > LABEL_CHARS
    line = short(AGE)
    assert line == "이 주민번호 900101-1234567로 고객 나이…"            # 자른 곳 앞의 공백은 지운다
    assert len(line) <= LABEL_CHARS and AGE.startswith(line[:-1])


def test_short_request_is_shown_whole_and_lines_are_joined():
    assert short("주문한 거 배송 언제 와요?") == "주문한 거 배송 언제 와요?"
    assert short("첫 줄\n\n둘째   줄") == "첫 줄 둘째 줄"                   # 목록은 한 줄이다
    assert short("가" * LABEL_CHARS) == "가" * LABEL_CHARS                  # 딱 맞으면 자르지 않는다


def test_request_list_on_screen_uses_the_ellipsis(screen_env):
    at = AppTest.from_string(DEMO, default_timeout=60).run()
    at.text_input(key="cli_text").set_value(AGE)
    next(b for b in at.button if b.label == "보내기").click().run()
    assert not at.exception
    assert at.selectbox[0].options == [f"[1] {short(AGE)}"]
