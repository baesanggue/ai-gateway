"""설정 로더: config.yaml(일반 설정) + .env(비밀값)을 하나의 설정 객체로 읽는다.

- config.yaml에는 비밀값을 넣지 않는다. API 키는 .env에서만 읽는다 (설계 결정 7번).
- 비밀값은 SecretStr로 감싸 로그·repr에 키가 찍히지 않게 한다.
- 다른 설정 파일을 쓰려면 환경변수 AIGW_CONFIG에 경로를 준다.
- 업종 프리셋(D22): config.yaml의 `preset: shop`이면 config/presets/shop.yaml을 기본값으로 깔고,
  config.yaml에 직접 적은 값이 그 위를 덮는다. 업종마다 달라지는 것(FAQ, 회사별 식별번호, 시연용 사전)만 프리셋에 둔다
"""
import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"
DEFAULT_ENV = ROOT / ".env"
PRESETS_DIR = ROOT / "config" / "presets"
PRESET_NAME = re.compile(r"[a-z][a-z0-9_]{0,30}")
TOKEN_KIND = re.compile(r"[가-힣A-Za-z_]{1,10}")   # 토큰 [종류N]의 종류. 숫자·괄호는 토큰 번호와 섞여 안 된다
# 반복 안에 또 반복: "(a+)+" 같은 모양은 긴 입력에서 처리 시간이 폭증한다 (#11 보안 검토와 같은 문제)
NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]")


class GatewayConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000


class CloudDecisionConfig(BaseModel):
    engine: Literal["openai_decisions"] = "openai_decisions"
    base_url: str = "https://api.openai.com/v1"
    model: str
    timeout_seconds: float = 10
    questions: list[str]
    needs_raw_only_if_masked: bool = True
    on_refusal: Literal["block", "premium", "cheap"] = "block"            # 위험·원본 필요 질문의 거부
    on_routing_refusal: Literal["block", "premium", "cheap"] = "premium"  # 난이도·의도 질문의 거부
    on_error_route: Literal["block", "premium", "cheap"] = "cheap"
    # 이전 대화 위험 판단에 클라이언트가 보낸 system·developer 메시지도 넣는다 (#12 보안 검토).
    # 회사가 모든 클라이언트를 통제하고 지시문 오탐이 문제가 될 때만 false로 끈다
    check_system_messages: bool = True


class DecisionConfig(BaseModel):
    # 로컬 판단 (Ollaya, /v1/systemone 형식)
    engine: Literal["ollaya", "jev"] = "ollaya"
    base_url: str
    model: str
    timeout_seconds: float = 10
    local_questions: list[str]
    # 클라우드 판단 (OpenAI Decisions, 가명화된 문장만)
    cloud: CloudDecisionConfig
    # 질문별 '예' 기준값
    thresholds: dict[str, float] = {}


class RoutingConfig(BaseModel):
    min_confidence: float | None = None
    low_confidence_route: Literal["premium", "block"] = "premium"
    # 차단 사유별 안내문. 사유 코드는 gateway/routing/policy.py의 BLOCK_PRIORITY
    block_messages: dict[str, str] = {}


class FAQEntry(BaseModel):
    id: str
    # 단어 묶음 목록. 한 묶음의 단어가 모두 들어 있으면 이 FAQ로 본다 (띄어쓰기 무시)
    keywords: list[list[str]]
    answer: str


class FAQConfig(BaseModel):
    """업종별 설정 (D13): 회사가 자기 FAQ와 정해 둔 답을 등록한다."""
    max_chars: int = 40                 # 이보다 긴 문장은 FAQ로 보지 않는다
    exclude_words: list[str] = []       # 업무 요청 표현이 있으면 FAQ로 보지 않는다 (띄어쓰기 무시)
    entries: list[FAQEntry] = []


class LLMModelConfig(BaseModel):
    provider: Literal["openai"] = "openai"
    model: str
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] | None = None
    price_per_1m_input: float
    price_per_1m_output: float


class LLMConfig(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 60
    placeholder_instruction: str | None = None   # 가명화된 요청에 붙이는 지시 (없으면 붙이지 않음)
    cheap: LLMModelConfig
    premium: LLMModelConfig


class CustomPattern(BaseModel):
    """회사별 식별번호 정규식 (#38). 예: 병원 차트번호 C + 8자리 → [차트번호1]. 프리셋이나 config.yaml에 적는다."""
    kind: str        # 토큰 이름
    pattern: str     # 정규식. 숫자는 [0-9]로 쓴다 (\d는 모든 문자권 숫자를 받는다)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if not TOKEN_KIND.fullmatch(v):
            raise ValueError("kind는 한글·영문 10자 이내여야 한다 (토큰 [종류N]의 종류)")
        return v

    @field_validator("pattern")
    @classmethod
    def _pattern(cls, v: str) -> str:
        if len(v) > 200:
            raise ValueError("정규식이 너무 길다 (200자 이내)")
        if NESTED_QUANTIFIER.search(v):
            raise ValueError("반복 안에 반복이 있는 정규식은 받지 않는다 (긴 입력에서 처리 시간이 폭증한다)")
        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"정규식 오류: {e}") from e
        return v


class MaskingConfig(BaseModel):
    user_dictionary_path: str
    # 시연용 가상 사전 (커밋됨, 업종 프리셋이 정한다). 시연할 때 user_dictionary_path로 복사해 쓴다 (D21)
    example_dictionary_path: str = "config/presets/shop.dictionary.example.txt"
    custom_patterns: list[CustomPattern] = []


class StorageConfig(BaseModel):
    sqlite_path: str


class AppConfig(BaseModel):
    preset: str | None = None        # 업종 프리셋 이름 (config/presets/<이름>.yaml, D22)
    security_level: Literal["basic", "strict", "local"] = "basic"
    gateway: GatewayConfig = GatewayConfig()
    decision: DecisionConfig
    routing: RoutingConfig = RoutingConfig()
    faq: FAQConfig = FAQConfig()
    llm: LLMConfig
    masking: MaskingConfig
    storage: StorageConfig


class Secrets(BaseSettings):
    """.env의 비밀값. 없으면 None (그 기능을 쓸 때 오류를 낸다)."""

    model_config = SettingsConfigDict(env_file=DEFAULT_ENV, env_file_encoding="utf-8", extra="ignore")

    openai_api_key: SecretStr | None = None
    xai_api_key: SecretStr | None = None
    # 게이트웨이를 쓰는 서비스에 나눠 줄 키(쉼표로 여러 개). 없으면 같은 PC에서 온 요청만 받는다 (D20)
    gateway_api_keys: SecretStr | None = None


class Settings(BaseModel):
    config: AppConfig
    secrets: Secrets


def load_settings(config_path: Path | None = None, env_file: Path | None = None) -> Settings:
    path = Path(config_path or os.environ.get("AIGW_CONFIG") or DEFAULT_CONFIG)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if raw.get("preset"):
        raw = _merge(load_preset(raw["preset"]), raw)     # 프리셋이 바탕, config.yaml이 위
    config = AppConfig.model_validate(raw)
    secrets = Secrets(_env_file=env_file or DEFAULT_ENV)
    return Settings(config=config, secrets=secrets)


def load_preset(name: str) -> dict:
    """업종 프리셋을 읽는다. 없는 이름이면 시작하지 않는다 (있는 프리셋을 알려 준다)."""
    available = sorted(p.stem for p in PRESETS_DIR.glob("*.yaml"))
    if not PRESET_NAME.fullmatch(str(name)) or not (PRESETS_DIR / f"{name}.yaml").exists():
        raise ValueError(f"업종 프리셋 '{name}'이(가) 없다. 쓸 수 있는 프리셋: {', '.join(available)}")
    return yaml.safe_load((PRESETS_DIR / f"{name}.yaml").read_text(encoding="utf-8")) or {}


def _merge(base: dict, over: dict) -> dict:
    """over가 base를 덮는다. 둘 다 dict인 값은 안쪽까지 합치고, 목록·값은 통째로 바꾼다."""
    out = dict(base)
    for key, value in over.items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out
