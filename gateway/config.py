"""설정 로더: config.yaml(일반 설정) + .env(비밀값)을 하나의 설정 객체로 읽는다.

- config.yaml에는 비밀값을 넣지 않는다. API 키는 .env에서만 읽는다 (설계 결정 7번).
- 비밀값은 SecretStr로 감싸 로그·repr에 키가 찍히지 않게 한다.
- 다른 설정 파일을 쓰려면 환경변수 AIGW_CONFIG에 경로를 준다.
"""
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"
DEFAULT_ENV = ROOT / ".env"


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


class LLMModelConfig(BaseModel):
    provider: Literal["openai"] = "openai"
    model: str
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] | None = None
    price_per_1m_input: float
    price_per_1m_output: float


class LLMConfig(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 60
    cheap: LLMModelConfig
    premium: LLMModelConfig


class MaskingConfig(BaseModel):
    user_dictionary_path: str


class StorageConfig(BaseModel):
    sqlite_path: str


class AppConfig(BaseModel):
    security_level: Literal["basic", "strict", "local"] = "basic"
    gateway: GatewayConfig = GatewayConfig()
    decision: DecisionConfig
    routing: RoutingConfig = RoutingConfig()
    llm: LLMConfig
    masking: MaskingConfig
    storage: StorageConfig


class Secrets(BaseSettings):
    """.env의 비밀값. 없으면 None (그 기능을 쓸 때 오류를 낸다)."""

    model_config = SettingsConfigDict(env_file=DEFAULT_ENV, env_file_encoding="utf-8", extra="ignore")

    openai_api_key: SecretStr | None = None
    xai_api_key: SecretStr | None = None


class Settings(BaseModel):
    config: AppConfig
    secrets: Secrets


def load_settings(config_path: Path | None = None, env_file: Path | None = None) -> Settings:
    path = Path(config_path or os.environ.get("AIGW_CONFIG") or DEFAULT_CONFIG)
    config = AppConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    secrets = Secrets(_env_file=env_file or DEFAULT_ENV)
    return Settings(config=config, secrets=secrets)
