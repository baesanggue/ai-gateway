"""설정 로더 검사: config.yaml 구조, 비밀값 읽기, 비밀값이 출력되지 않는지."""
from pathlib import Path

import pytest
from pydantic import ValidationError

from gateway.config import load_settings

ROOT = Path(__file__).resolve().parent.parent


def test_repo_config_loads(tmp_path):
    s = load_settings(ROOT / "config.yaml", env_file=tmp_path / "없는.env")
    c = s.config
    assert c.security_level in ("basic", "strict", "local")
    assert c.decision.model == "gliclass:v4-edge"
    assert set(c.decision.local_questions) == {"has_pii", "is_confidential"}
    assert c.decision.cloud.model == "gpt-6-luna"
    assert {"has_pii", "is_confidential", "needs_raw_value", "is_risky", "difficulty_complex"} <= set(c.decision.thresholds)
    # .env가 없으면 키는 None
    assert s.secrets.openai_api_key is None


def test_secrets_are_read_but_never_printed(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=test-openai-key-123\nXAI_API_KEY=test-xai-key-456\n", encoding="utf-8")
    s = load_settings(ROOT / "config.yaml", env_file=env)
    assert s.secrets.openai_api_key.get_secret_value() == "test-openai-key-123"
    # repr·문자열 변환·JSON 직렬화 어디에도 키가 나오면 안 된다
    for text in (repr(s), str(s), s.model_dump_json()):
        assert "test-openai-key-123" not in text
        assert "test-xai-key-456" not in text


def test_invalid_security_level_is_rejected(tmp_path):
    bad = tmp_path / "config.yaml"
    text = (ROOT / "config.yaml").read_text(encoding="utf-8").replace("security_level: basic", "security_level: unsafe")
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(bad, env_file=tmp_path / "없는.env")
