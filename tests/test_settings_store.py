"""설정 화면의 저장·적용 (#78, D26): 저장 → 게이트웨이 적용, 실패하면 저장 전으로 되돌린다."""
from pathlib import Path

import pytest

from admin import settings_store as store
from admin.settings_store import Result
from gateway.config import load_settings

ROOT = Path(__file__).resolve().parent.parent


def validator(tmp_path, override):
    return lambda: load_settings(ROOT / "config.yaml", env_file=tmp_path / "없는.env", override_path=override)


def test_override_with_keeps_other_values():
    base = {"security_level": "strict", "decision": {"trusted_system_prompts": ["a.txt"]}}
    out = store.override_with(base, preset="medical")
    assert out == {"security_level": "strict", "preset": "medical", "decision": {"trusted_system_prompts": ["a.txt"]}}
    assert store.override_with(out, prompts=[])["decision"] == {"trusted_system_prompts": []}
    with pytest.raises(ValueError):
        store.override_with({}, level="unsafe")
    with pytest.raises(ValueError):
        store.override_with({}, preset="../../etc")


def test_check_dictionary_counts_kinds_and_reports_line_only():
    assert store.check_dictionary("# 설명\n고객,가나다\n직원,라마바\n고객,사아자\n") == {"고객": 2, "직원": 1}
    with pytest.raises(ValueError) as e:
        store.check_dictionary("고객,가나다\n고객,차\n")             # 한 글자 값
    assert "2번째 줄" in str(e.value) and "차" not in str(e.value)   # 값(이름)은 메시지에 넣지 않는다


def test_apply_saves_and_reloads(tmp_path):
    over = tmp_path / "settings.override.yaml"
    calls = []
    result = store.apply({over: "security_level: strict\n"}, "http://gw",
                         reload=lambda url: calls.append(url) or Result(True, "적용"), validate=validator(tmp_path, over))
    assert result.ok and calls == ["http://gw"]
    assert over.read_text(encoding="utf-8") == "security_level: strict\n"


def test_apply_restores_files_when_gateway_refuses(tmp_path):
    """게이트웨이가 새 설정으로 만들어지지 않으면(사전 오류 등) 저장 전 내용으로 되돌린다. 지금 설정은 그대로 돈다."""
    over, dictionary = tmp_path / "settings.override.yaml", tmp_path / "dict.txt"
    over.write_text("security_level: basic\n", encoding="utf-8")
    dictionary.write_bytes("고객,가나다\n".encode("utf-8"))
    result = store.apply({over: "security_level: strict\n", dictionary: "고객,라마바\n"}, "http://gw",
                         reload=lambda url: Result(False, "거부"), validate=validator(tmp_path, over))
    assert not result.ok
    assert over.read_text(encoding="utf-8") == "security_level: basic\n"
    assert dictionary.read_bytes() == "고객,가나다\n".encode("utf-8")


def test_apply_restores_when_settings_are_invalid_without_calling_gateway(tmp_path):
    over = tmp_path / "settings.override.yaml"                       # 저장 전에는 없던 파일
    called = []
    result = store.apply({over: "storage:\n  sqlite_path: x.db\n"}, "http://gw",
                         reload=lambda url: called.append(1), validate=validator(tmp_path, over))
    assert not result.ok and "바꿀 수 없는 설정" in result.message
    assert not over.exists() and called == []


def test_apply_when_gateway_is_off_keeps_files_for_next_start(tmp_path):
    over = tmp_path / "settings.override.yaml"
    result = store.apply({over: "preset: school\n"}, "http://gw", reload=lambda url: None,
                         validate=validator(tmp_path, over))
    assert result.ok and "다음에 켤 때" in result.message
    assert over.exists()


def test_apply_none_deletes_and_restore_brings_it_back(tmp_path):
    prompt, over = tmp_path / "trusted_prompt.txt", tmp_path / "settings.override.yaml"
    prompt.write_text("너는 쇼핑몰 상담원이다.", encoding="utf-8")
    result = store.apply({prompt: None, over: "decision:\n  trusted_system_prompts: []\n"}, "http://gw",
                         reload=lambda url: Result(False, "거부"), validate=validator(tmp_path, over))
    assert not result.ok and prompt.read_text(encoding="utf-8") == "너는 쇼핑몰 상담원이다."
    assert not over.exists()


def test_write_text_keeps_lf(tmp_path):
    p = tmp_path / "a.txt"
    store.write_text(p, "한 줄\n두 줄\n")
    assert p.read_bytes() == "한 줄\n두 줄\n".encode("utf-8")       # Windows에서도 CRLF로 바꾸지 않는다


def test_reload_gateway_without_token_file_counts_as_off(tmp_path):
    assert store.reload_gateway("http://127.0.0.1:9", token_path=tmp_path / "없음.token") is None
