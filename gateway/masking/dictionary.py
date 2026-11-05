"""사용자 사전 (#44): 회사가 등록한 고객명·직원명·사내 고유명사. 정규식으로 못 잡는 이름을 가린다.

형식 (한 줄에 하나): 종류,값   예) 고객,김민수 / 직원,이서연 / 사내,X-프로젝트
- 종류가 토큰 이름이 된다 (고객 → [고객1]). 쉼표가 없으면 종류를 고객으로 본다
- 빈 줄과 #으로 시작하는 줄은 건너뛴다
- 실제 사전은 private/(커밋 안 함). 없으면 게이트웨이를 시작하지 않는다 (D21). 시연할 때는 시연용 가상 사전
  (업종 프리셋의 config/presets/<프리셋>.dictionary.example.txt)를 그 자리로 복사한다
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


class DictionaryMissing(RuntimeError):
    """사용자 사전이 없다. 게이트웨이를 시작하지 않는다 (D21)."""


def load(cfg: MaskingConfig) -> tuple[list[Entry], Path, bool]:
    """(항목, 읽은 파일, 시연용 사전과 같은 내용인가).

    사용자 사전이 없으면 시작하지 않는다(D21). 예전처럼 시연용 사전으로 조용히 넘어가면, 회사가 사전을 만들지 않고
    배포했을 때 가상 이름 다섯 개로 돌면서 실제 고객 이름이 경고 한 줄만 남기고 그대로 나간다(11/02 보안 검토).
    시연할 때는 시연용 사전을 사용자 사전 자리로 복사한다(설치 스크립트가 대신 한다). 그 상태는 경고로 알린다."""
    real, example = _path(cfg.user_dictionary_path), _path(cfg.example_dictionary_path)
    if not real.exists():
        raise DictionaryMissing(
            f"사용자 사전 {cfg.user_dictionary_path}이(가) 없어 게이트웨이를 시작하지 않습니다. "
            f"시연이라면 시연용 가상 사전을 복사하세요: copy {Path(cfg.example_dictionary_path)} "
            f"{Path(cfg.user_dictionary_path)} / 운영이라면 회사의 고객명·직원명을 '종류,값' 형식으로 넣으세요.")
    text = real.read_text(encoding="utf-8-sig")
    is_example = example.exists() and text == example.read_text(encoding="utf-8-sig")
    if is_example:
        logger.warning("시연용 가상 사전으로 동작 중입니다(%s). 실제 고객명은 가려지지 않습니다. "
                       "운영 전에 회사의 고객명·직원명으로 바꾸세요.", cfg.user_dictionary_path)
    return parse(text), real, is_example


def _path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else ROOT / path
