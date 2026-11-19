"""첫 화면 안내와 시연 예시 단추 (#131). 설문(D31)에서 10명 중 5명이 처음에 무엇을 해야 할지 몰랐다.

- 예시는 업종 프리셋의 display.examples에 있다. 예시가 보여 주려는 경로로 실제로 가는지 코드 단계(FAQ·가명화)까지 확인한다
  (원본 필요 판단·이름 인식 모델이 필요한 부분은 클라우드·모델 파일이 있어야 해서 여기서는 보지 않는다)
- 화면은 streamlit AppTest로 돌린다. 게이트웨이를 부르는 _send는 가짜로 바꿔 밖으로 요청이 나가지 않게 한다
"""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from admin.overview_page import _steps_html  # noqa: E402
from gateway.config import DemoExample, DisplayConfig, load_settings  # noqa: E402
from gateway.masking.dictionary import parse  # noqa: E402
from gateway.masking.masker import RuleMasker  # noqa: E402
from gateway.routing.policy import match_faq  # noqa: E402

PRESETS = ["shop", "medical", "school"]


def _config(tmp_path: Path, preset: str = "shop", db: Path | None = None) -> Path:
    text = (ROOT / "config.yaml").read_text(encoding="utf-8").replace("preset: shop", f"preset: {preset}", 1)
    if db is not None:
        text = text.replace("sqlite_path: data/gateway.db", f"sqlite_path: {db.as_posix()}", 1)
    path = tmp_path / f"config-{preset}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _load(tmp_path: Path, preset: str):
    return load_settings(_config(tmp_path, preset), env_file=tmp_path / "없는.env").config


def _masked(cfg, text: str) -> str:
    entries = parse((ROOT / cfg.masking.example_dictionary_path).read_text(encoding="utf-8-sig"))
    return RuleMasker(entries).mask([{"role": "user", "content": text}]).messages[0]["content"]


@pytest.mark.parametrize("preset", PRESETS)
def test_each_preset_has_four_examples(tmp_path, preset):
    examples = _load(tmp_path, preset).display.examples
    assert len(examples) == 4 and all(e.title and e.desc and e.text for e in examples)


@pytest.mark.parametrize("preset", PRESETS)
def test_only_the_first_example_gets_the_faq_answer(tmp_path, preset):
    cfg = _load(tmp_path, preset)
    first, *rest = cfg.display.examples
    assert match_faq(first.text, cfg.faq) is not None          # ① 정해 둔 답 (LLM 없이 0원)
    assert all(match_faq(e.text, cfg.faq) is None for e in rest)   # ②~④가 정해 둔 답으로 새면 시연이 틀어진다


@pytest.mark.parametrize("preset", PRESETS)
def test_pii_examples_are_masked_by_dictionary_and_patterns(tmp_path, preset):
    cfg = _load(tmp_path, preset)
    ex = cfg.display.examples
    second, third, fourth = _masked(cfg, ex[1].text), _masked(cfg, ex[2].text), _masked(cfg, ex[3].text)
    assert "김민수" not in second and "010-1234-5678" not in second     # ② 사전 이름 + 전화번호
    assert "900101-1234567" not in third                                 # ③ 주민등록번호 (원본 필요 판단은 클라우드)
    assert "가상로 123" not in fourth                                    # ④ 주소. 박지훈은 이름 인식 모델이 가린다


def test_examples_fit_one_row():
    one = DemoExample(title="t", desc="d", text="x")
    assert len(DisplayConfig(examples=[one] * 4).examples) == 4
    with pytest.raises(ValidationError):
        DisplayConfig(examples=[one] * 5)


def test_guide_steps_follow_the_examples(tmp_path):
    html = _steps_html(_load(tmp_path, "shop").display.examples)
    assert "① 배송 문의 → ② 사과 메일 → ③ 나이 계산 → ④ 자유롭게" in html
    assert "입력 칸에 요청을 쓰고" in _steps_html([])                     # 예시가 없는 업종도 안내는 나온다


DEMO = f"""
import sys
sys.path.insert(0, r"{ROOT}")
import streamlit as st
from admin import demo_page

def fake_send(s, text):            # 게이트웨이를 부르지 않는다
    st.session_state.turns.append({{"user": text, "answer": "답", "request_id": None, "route": "ok"}})
    st.session_state.selected = len(st.session_state.turns) - 1

demo_page._send = fake_send
demo_page.page()
"""


@pytest.fixture
def screen_env(tmp_path, monkeypatch):
    """체험판을 끄고(BETA_PASSWORD 빈 값이 .env를 덮는다) 빈 요청 기록으로 화면을 연다."""
    from admin.ui import settings
    monkeypatch.setenv("BETA_PASSWORD", "")
    monkeypatch.setenv("AIGW_CONFIG", str(_config(tmp_path, db=tmp_path / "empty.db")))
    settings.clear()
    yield
    settings.clear()


def test_example_button_fills_input_and_marks_done_after_sending(screen_env):
    at = AppTest.from_string(DEMO, default_timeout=60).run()
    assert not at.exception
    text = at.button(key="exbtn_1").help                   # ② 사과 메일
    at.button(key="exbtn_1").click().run()
    assert at.text_input(key="cli_text").value == text        # 넣기만 하고 보내지는 않는다 (11/19 사용자 선택)
    assert at.session_state.turns == []
    at.text_input(key="cli_text").set_value(text + " 정중하게")   # 고쳐서 보내도
    next(b for b in at.button if b.label == "보내기").click().run()
    assert at.session_state.turns[-1]["user"] == text + " 정중하게"
    assert at.session_state.tried == {1}                       # 그 예시는 해 본 것으로 센다
    assert "완료" in at.button(key="exbtn_1").label and "완료" not in at.button(key="exbtn_0").label


def test_first_screen_guide_shows_until_closed(screen_env):
    at = AppTest.from_file(str(ROOT / "admin" / "app.py"), default_timeout=60).run()
    assert not at.exception
    assert any("처음 쓰시나요" in m.value for m in at.markdown)     # 요청이 없으면 펼친 안내
    at.button(key="guide_close").click().run()
    assert not any("처음 쓰시나요" in m.value for m in at.markdown)
    assert [e.label for e in at.expander] == ["처음 쓰는 방법 보기"]   # 닫으면 접힌 한 줄
