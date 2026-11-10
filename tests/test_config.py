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
    assert c.decision.local_questions == ["is_confidential"]     # 남은 개인정보는 이름 인식 모델이 본다 (D23)
    assert (c.masking.ner.model_dir, c.masking.ner.onnx_file) == ("models/koelectra-ko-pii-ner", "onnx/model_int8.onnx")
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


# ── 화면에서 바꾼 설정 (D26, #78) ──

def test_override_changes_level_preset_and_prompts(tmp_path):
    over = tmp_path / "settings.override.yaml"
    over.write_text("security_level: strict\npreset: medical\n"
                    "decision:\n  trusted_system_prompts: [private/trusted_prompt.txt]\n", encoding="utf-8")
    c = load_settings(ROOT / "config.yaml", env_file=tmp_path / "없는.env", override_path=over).config
    assert c.security_level == "strict"
    assert c.preset == "medical"
    assert "금식" in c.masking.not_names                       # 업종을 바꾸면 그 프리셋의 값이 바탕이 된다
    assert c.decision.trusted_system_prompts == ["private/trusted_prompt.txt"]
    assert c.decision.model == "gliclass:v4-edge"              # 나머지 decision 값은 config.yaml 그대로


def test_override_rejects_other_keys(tmp_path):
    over = tmp_path / "settings.override.yaml"
    for text in ("storage:\n  sqlite_path: x.db\n", "decision:\n  model: other\n", "decision: strict\n"):
        over.write_text(text, encoding="utf-8")
        with pytest.raises(ValueError):
            load_settings(ROOT / "config.yaml", env_file=tmp_path / "없는.env", override_path=over)


def test_override_is_not_used_when_config_path_is_given(tmp_path, monkeypatch):
    # 시험·측정은 경로를 직접 준다. 화면에서 바꾼 설정이 결과를 바꾸면 안 된다
    import gateway.config as config
    over = tmp_path / "settings.override.yaml"
    over.write_text("security_level: strict\n", encoding="utf-8")
    monkeypatch.setattr(config, "DEFAULT_OVERRIDE", over)
    assert load_settings(ROOT / "config.yaml", env_file=tmp_path / "없는.env").config.security_level == "basic"
    monkeypatch.setenv("AIGW_CONFIG", str(ROOT / "config.yaml"))
    assert config.load_settings(env_file=tmp_path / "없는.env").config.security_level == "strict"
