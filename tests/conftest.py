"""모든 시험에 공통인 준비."""
import pytest

from tests.fakes import FakeNer


@pytest.fixture(autouse=True)
def fake_ner_model(monkeypatch):
    """게이트웨이는 이름 인식 모델 파일이 없으면 시작하지 않는다(D23). 모델 파일은 저장소에 없으므로(models/는 .gitignore)
    설정으로 가명화 부품을 만드는 시험은 아무것도 찾지 않는 가짜 모델을 쓴다. 모델 없는 PC에서도 시험이 돈다.
    실제 모델 시험은 tests/test_ner.py(파일이 있을 때만), 시작 거부 시험은 이 대체를 되돌려 진짜 불러오기를 쓴다."""
    monkeypatch.setattr("gateway.masking.masker.load_ner", lambda cfg: FakeNer())
