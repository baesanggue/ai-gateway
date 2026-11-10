"""관리 토큰 (D26): 설정 다시 읽기(/admin/reload)를 부를 수 있는 프로그램을 가린다.

게이트웨이가 켤 때마다 새로 만들어 private/admin.token(커밋 안 함)에 쓰고, 설정 화면이 읽어 헤더로 보낸다.
설정 자체가 이 PC의 파일이므로 "이 PC의 파일을 읽을 수 있는 프로그램"만 설정을 다시 읽게 한다.
주소(127.0.0.1)만 믿으면 같은 PC의 프록시를 거친 외부 요청도 통과한다 (11/09 보안 검토).
켤 때마다 바뀌는 값이라 .env에 두는 설정 비밀값(API 키)과 다르다. 화면·로그에 값을 내지 않는다.
"""
import os
import secrets
from pathlib import Path

from gateway.config import ROOT

ADMIN_TOKEN_PATH = ROOT / "private" / "admin.token"
ADMIN_TOKEN_HEADER = "x-admin-token"


def write_admin_token(path: Path) -> str:
    """새 토큰을 만들어 파일에 쓴다 (한 번에 바꾼다: 임시 파일 → 이름 바꾸기)."""
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(token, encoding="utf-8")
    os.replace(tmp, path)
    return token


def read_admin_token(path: Path | None = None) -> str | None:
    """설정 화면이 읽는다. 게이트웨이를 한 번도 켜지 않았으면 None."""
    try:
        return (path or ADMIN_TOKEN_PATH).read_text(encoding="utf-8").strip()
    except OSError:
        return None
