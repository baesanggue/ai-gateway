"""업종 프리셋 검사 (D22, #38): 설정 한 줄로 업종을 바꾸고, 회사별 식별번호를 가린다."""
import re

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gateway.config import ROOT, CustomPattern, load_settings
from gateway.main import create_app
from gateway.masking.dictionary import Entry
from gateway.masking.masker import RuleMasker
from gateway.routing.policy import match_faq
from gateway.storage.log import RequestLog
from tests.fakes import TEST_KEY, FakeUpstream


def settings_for(tmp_path, preset, extra=""):
    """루트 config.yaml에서 preset 줄만 바꾼 설정. 사용자 사전 자리에는 그 프리셋의 시연용 사전."""
    text = (ROOT / "config.yaml").read_text(encoding="utf-8").replace("preset: shop", f"preset: {preset}") + extra
    (tmp_path / "config.yaml").write_text(text, encoding="utf-8")
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY={TEST_KEY}\n", encoding="utf-8")
    s = load_settings(tmp_path / "config.yaml", env_file=tmp_path / ".env")
    s.config.masking.user_dictionary_path = str(ROOT / s.config.masking.example_dictionary_path)
    return s


@pytest.mark.parametrize("preset, question, faq_id, kind", [
    ("shop", "주문한 거 배송 언제 와요?", "shipping", "회원번호"),
    ("medical", "토요일에도 진료하나요?", "hours", "차트번호"),
    ("school", "휴학 신청은 어디서 해요?", "leave", "학번"),
])
def test_one_line_switches_faq_and_identifier(tmp_path, preset, question, faq_id, kind):
    cfg = settings_for(tmp_path, preset).config
    assert cfg.preset == preset
    assert match_faq(question, cfg.faq).faq_id == faq_id
    assert [p.kind for p in cfg.masking.custom_patterns] == [kind]


def test_medical_preset_keeps_clinic_words_unmasked_through_from_config(tmp_path, monkeypatch):
    """의료 프리셋의 "이름이 아닌 낱말"이 설정 → 가명화 부품까지 이어진다 (#65, #59 M007)."""
    from tests.fakes import FakeNer
    monkeypatch.setattr("gateway.masking.masker.load_ner", lambda cfg: FakeNer(names=["금식", "양미래"]))
    masker = RuleMasker.from_config(settings_for(tmp_path, "medical").config.masking)
    out = masker.mask([{"role": "user", "content": "건강검진 전날 금식은 몇 시부터 해야 하나요? 양미래 환자"}])
    assert out.messages[0]["content"] == "건강검진 전날 금식은 몇 시부터 해야 하나요? [이름1] 환자"


def test_default_config_is_the_shop_preset():
    cfg = load_settings(ROOT / "config.yaml", env_file=ROOT / "없는.env").config
    assert cfg.preset == "shop" and len(cfg.faq.entries) == 5


def test_medical_scenario_masks_chart_number_and_restores(tmp_path):
    """병원: 환자 이름(사전)과 차트번호(회사별 식별번호)가 가려진 채 나가고, 답변에서 되돌아온다."""
    up = FakeUpstream(reply="[환자1]님, 검사 결과가 나왔습니다. 차트번호 [차트번호1]로 확인하세요.")
    app = create_app(settings_for(tmp_path, "medical"), transport=httpx.MockTransport(up),
                     log=RequestLog(tmp_path / "gateway.db"))
    with TestClient(app) as client:
        r = client.post("/v1/chat/completions", json={"model": "gpt-test", "messages": [
            {"role": "user", "content": "김민수 환자(C12345678)에게 검사 결과 안내 문자 써 줘"}]})
    sent = up.bodies("/v1/chat/completions")[0]["messages"][-1]["content"]
    assert sent == "[환자1] 환자([차트번호1])에게 검사 결과 안내 문자 써 줘"
    assert b"C12345678" not in up.raw_bytes() and "김민수".encode() not in up.raw_bytes()
    assert r.json()["choices"][0]["message"]["content"] == "김민수님, 검사 결과가 나왔습니다. 차트번호 C12345678로 확인하세요."


def test_config_yaml_overrides_the_preset(tmp_path):
    extra = ("\nfaq:\n  entries:\n    - id: only\n      keywords: [[주차]]\n      answer: 우리 회사 주차 안내\n")
    cfg = settings_for(tmp_path, "medical", extra).config
    assert [e.id for e in cfg.faq.entries] == ["only"]                 # 목록은 통째로 바뀐다
    assert [p.kind for p in cfg.masking.custom_patterns] == ["차트번호"]  # 적지 않은 값은 프리셋 그대로


def test_unknown_preset_refuses_to_start(tmp_path):
    with pytest.raises(ValueError) as e:
        settings_for(tmp_path, "bank")
    assert "shop" in str(e.value) and "medical" in str(e.value)       # 쓸 수 있는 프리셋을 알려 준다


@pytest.mark.parametrize("kind, pattern, message", [
    ("차트번호1", "C[0-9]{8}", "kind"),                                 # 숫자는 토큰 번호와 섞인다
    ("차트번호", "(C[0-9]{8}", "정규식 오류"),                         # 닫히지 않은 괄호
    ("차트번호", "(C[0-9]+)+", "반복 안에 반복"),                       # 긴 입력에서 처리 시간 폭증
])
def test_bad_custom_patterns_are_rejected(kind, pattern, message):
    with pytest.raises(ValidationError) as e:
        CustomPattern(kind=kind, pattern=pattern)
    assert message in str(e.value)


def test_custom_identifier_in_compat_form_is_reported_as_unmasked():
    """동그라미 글자 Ⓒ(NFKC로 C)로 쓴 차트번호는 가리지 못한다. 마지막 검사가 찾아 차단하게 한다."""
    masker = RuleMasker([Entry("환자", "김민수")], custom_patterns=[("차트번호", re.compile(r"(?<![A-Za-z0-9])C[0-9]{8}(?![0-9])"))])
    r = masker.mask([{"role": "user", "content": "Ⓒ12345678 환자 기록 요약해 줘"}])
    assert r.unmasked == ["차트번호"]
    ok = masker.mask([{"role": "user", "content": "C12345678 환자 기록 요약해 줘"}])
    assert ok.messages[0]["content"] == "[차트번호1] 환자 기록 요약해 줘" and ok.unmasked == []
