"""설정 화면(#78)이 파일에 저장하고 게이트웨이에 적용하는 일 (D26). Streamlit과 떼어 시험한다.

- 화면에서 바꾸는 것: 보안 수준·업종 프리셋·등록 지시문 → private/settings.override.yaml, 사용자 사전 → 사전 파일
- 저장한 뒤 게이트웨이에 "설정 다시 읽기"(/admin/reload)를 부른다. 다시 시작하지 않아도 다음 요청부터 새 설정이다
- 게이트웨이가 새 설정으로 만들어지지 않으면(사전 형식 오류 등) 저장 전 파일로 되돌린다. 지금 설정은 그대로 돈다
- 게이트웨이가 꺼져 있으면 이 화면에서 설정을 읽어 확인만 하고, 다음에 켤 때 적용된다
- 사전 내용은 오류 메시지에 넣지 않는다(줄 번호만, dictionary.parse가 그렇게 만든다)
"""
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

from gateway import config as gw_config
from gateway.admin_token import ADMIN_TOKEN_HEADER, read_admin_token
from gateway.masking.dictionary import parse

ROOT = gw_config.ROOT
LEVELS = ("basic", "strict", "local")
PROMPT_FILE = "private/trusted_prompt.txt"       # 화면에서 등록하는 지시문 (커밋 안 함)


@dataclass
class Result:
    ok: bool
    message: str


def presets() -> list[str]:
    return sorted(p.stem for p in gw_config.PRESETS_DIR.glob("*.yaml"))


def resolve(p: str) -> Path:
    return Path(p) if Path(p).is_absolute() else ROOT / p


def read_override(path: Path | None = None) -> dict:
    return gw_config.load_override(path or gw_config.DEFAULT_OVERRIDE)


def write_text(path: Path, text: str) -> None:
    """한 번에 바꾼다 (임시 파일 → 이름 바꾸기). 쓰다가 멈춰도 반쯤 쓴 파일이 남지 않는다. 줄바꿈은 LF 그대로."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, path)


def override_with(base: dict, *, level: str | None = None, preset: str | None = None,
                  prompts: list[str] | None = None) -> dict:
    """화면 설정 파일 내용에 바꾼 값만 넣는다 (나머지는 그대로)."""
    out = dict(base)
    if level is not None:
        if level not in LEVELS:
            raise ValueError(f"보안 수준은 {', '.join(LEVELS)} 중 하나")
        out["security_level"] = level
    if preset is not None:
        if preset not in presets():
            raise ValueError(f"업종 프리셋 '{preset}'이(가) 없다")
        out["preset"] = preset
    if prompts is not None:
        out["decision"] = {**out.get("decision", {}), "trusted_system_prompts": prompts}
    return out


def check_dictionary(text: str) -> dict[str, int]:
    """사전 형식을 확인하고 종류별 개수를 돌려준다. 틀리면 ValueError(줄 번호만 들어 있다)."""
    counts: dict[str, int] = {}
    for e in parse(text):
        counts[e.kind] = counts.get(e.kind, 0) + 1
    return counts


# ── 저장 + 적용 ──

def snapshot(paths: list[Path]) -> dict[Path, bytes | None]:
    return {p: (p.read_bytes() if p.exists() else None) for p in paths}


def restore(snap: dict[Path, bytes | None]) -> None:
    for p, data in snap.items():
        if data is None:
            p.unlink(missing_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)


def reload_gateway(url: str, token_path: Path | None = None) -> Result | None:
    """게이트웨이에 설정을 다시 읽게 한다. 꺼져 있으면 None.
    게이트웨이가 켤 때 쓴 관리 토큰(private/admin.token)을 헤더로 보낸다 (D26). 토큰 값은 화면·로그에 내지 않는다."""
    token = read_admin_token(token_path)
    if token is None:
        return None                        # 게이트웨이를 한 번도 켜지 않았다
    try:
        r = httpx.post(f"{url}/admin/reload", headers={ADMIN_TOKEN_HEADER: token}, timeout=60)
    except httpx.HTTPError:
        return None
    if r.status_code == 200:
        return Result(True, "적용했습니다. 다음 요청부터 새 설정을 씁니다.")
    try:
        message = r.json().get("message", "")
    except ValueError:
        message = f"상태 코드 {r.status_code}"
    return Result(False, f"게이트웨이가 새 설정을 받지 않아 저장 전으로 되돌렸습니다: {message}")


def apply(files: dict[Path, str | None], url: str, reload: Callable[[str], Result | None] = reload_gateway,
          validate: Callable[[], object] = gw_config.load_settings) -> Result:
    """파일들을 저장하고 게이트웨이에 적용한다. 설정이 틀리거나 게이트웨이가 받지 않으면 저장 전으로 되돌린다.

    files: {경로: 새 내용}. None이면 그 파일을 지운다(등록 지시문 해제).
    """
    snap = snapshot(list(files))
    for path, text in files.items():
        if text is None:
            path.unlink(missing_ok=True)
        else:
            write_text(path, text)
    try:
        validate()                         # 게이트웨이가 꺼져 있어도 설정 형식·프리셋·바꿀 수 있는 키는 여기서 걸린다
    except (ValueError, RuntimeError, OSError, yaml.YAMLError) as e:
        restore(snap)
        return Result(False, f"설정이 올바르지 않아 저장하지 않았습니다: {e}")
    result = reload(url)
    if result is None:
        return Result(True, "저장했습니다. 게이트웨이가 꺼져 있어, 다음에 켤 때 적용됩니다.")
    if not result.ok:
        restore(snap)
    return result
