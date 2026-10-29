"""OpenAI 공식 SDK로 호출해도 동작하는지 검사.

"기존 서비스는 API 주소(base_url)만 바꾸면 된다"(설계 결정 4번)를 매번 자동으로 확인한다.
TestClient는 httpx.Client라서 SDK의 http_client로 넘기면 서버를 띄우지 않고 메모리 안에서 호출된다.
"""
from pathlib import Path

import openai
import pytest
from fastapi.testclient import TestClient

from gateway.config import load_settings
from gateway.main import create_app

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def sdk(tmp_path):
    app = create_app(load_settings(ROOT / "config.yaml", env_file=tmp_path / "없는.env"))
    return openai.OpenAI(base_url="http://testserver/v1", api_key="test-only",
                         http_client=TestClient(app), max_retries=0)


def test_sdk_chat_completion_works_with_only_base_url_changed(sdk):
    res = sdk.chat.completions.create(
        model="gpt-test",
        messages=[{"role": "user", "content": "김민수 고객에게 배송 지연 사과 메일 써줘"}],
    )
    assert res.object == "chat.completion"
    assert res.choices[0].message.role == "assistant"
    assert res.choices[0].message.content


def test_sdk_raises_bad_request_for_stream(sdk):
    with pytest.raises(openai.BadRequestError) as e:
        sdk.chat.completions.create(model="gpt-test", stream=True,
                                    messages=[{"role": "user", "content": "안녕"}])
    assert "stream" in str(e.value)
