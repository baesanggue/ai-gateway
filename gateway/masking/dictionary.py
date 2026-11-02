"""사용자 사전 (#44): 회사가 등록한 고객명·직원명·사내 고유명사. 정규식으로 못 잡는 이름을 가린다.

형식 (한 줄에 하나): 종류,값   예) 고객,김민수 / 직원,이서연 / 사내,X-프로젝트
- 종류가 토큰 이름이 된다 (고객 → [고객1]). 쉼표가 없으면 종류를 고객으로 본다
- 빈 줄과 #으로 시작하는 줄은 건너뛴다
- 실제 사전은 private/(커밋 안 함). 없으면 시연용 가상 사전을 쓰고 경고를 남긴다 (11/02 결정)
- 오류 메시지에는 줄 번호만 넣는다. 값(실제 이름)은 로그·화면에 남기지 않는다
"""
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from gateway.config import ROOT, MaskingConfig

logger = logging.getLogger(__name__)
KIND = re.compile(r"[가-힣A-Za-z_]{1,10}")   # 토큰 [종류N]의 종류. 숫자·괄호는 토큰 번호와 섞여 안 된다


@dataclass(frozen=True)
class Entry:
    kind: str
    value: str


def parse(text: str) -> list[Entry]:
    entries, seen = [], set()
    for no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        kind, value = (part.strip() for part in line.split(",", 1)) if "," in line else ("고객", line)
        if not KIND.fullmatch(kind):
            raise ValueError(f"사용자 사전 {no}번째 줄: 종류는 한글·영문 10자 이내여야 한다")
        if len(value) < 2:
            raise ValueError(f"사용자 사전 {no}번째 줄: 값이 너무 짧다 (한 글자는 다른 낱말까지 가린다)")
        if value not in seen:           # 같은 값이 두 번이면 처음 것을 쓴다
            seen.add(value)
            entries.append(Entry(kind, value))
    return entries


def load(cfg: MaskingConfig) -> tuple[list[Entry], Path, bool]:
    """(항목, 읽은 파일, 시연용 사전인가). 실제 사전이 없으면 시연용 가상 사전을 읽는다."""
    real, example = _path(cfg.user_dictionary_path), _path(cfg.example_dictionary_path)
    if real.exists():
        return parse(real.read_text(encoding="utf-8-sig")), real, False
    logger.warning("사용자 사전(%s)이 없어 시연용 가상 사전(%s)을 씁니다. 실제 고객명은 가려지지 않습니다.",
                   cfg.user_dictionary_path, cfg.example_dictionary_path)
    return (parse(example.read_text(encoding="utf-8-sig")) if example.exists() else []), example, True


def _path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else ROOT / path
