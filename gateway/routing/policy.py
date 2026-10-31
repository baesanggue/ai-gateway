"""라우팅 정책: 판단 결과와 보안 수준으로 처리 경로(템플릿·저렴·고성능·차단)를 정한다 (#10).

경로를 정하는 곳은 요청 처리 순서(D12)에 맞춰 세 군데다.
1. match_faq: 가명화 뒤, 판단 전. 등록된 FAQ면 템플릿으로 답하고 밖으로 아무것도 보내지 않는다
2. decide_after_local: 로컬 판단 뒤. 엄격 수준에서 기밀·남은 개인정보 의심이면 클라우드에 보내기 전에 차단
3. decide_after_cloud: 클라우드 판단 뒤. 차단 / 고성능 / 저렴

차단 사유가 여러 개면 모두 reasons에 남기고(로그·화면용), 사용자에게는 가장 앞선 사유의 안내문 하나를 보낸다.
예: 인젝션 시도는 대개 업무와도 무관하다. 이때 "업무와 무관합니다"보다 "보안 정책상 처리할 수 없습니다"가
정확한 안내이고, 관리자 화면에는 두 사유가 다 보여야 판단 근거가 남는다 (10/31 결정).
"""
import re
from dataclasses import dataclass, field
from typing import Literal

from gateway.config import AppConfig, FAQConfig
from gateway.decision.questions import ROUTING_QUESTIONS
from gateway.decision.service import JudgeResult, Judgment

Route = Literal["template", "cheap", "premium", "block"]
SecurityLevel = Literal["basic", "strict", "local"]

# 차단 사유의 우선순위. 사용자에게는 이 순서에서 가장 앞선 사유의 안내문을 보낸다.
# 확실하게 판단된 보안 사유 → 판단을 거부한 경우 → 비용 문제(무관) 순서다.
# 로컬 단계(기밀·남은 개인정보)와 클라우드 단계 사유는 한 요청에서 같이 나오지 않는다 (로컬에서 막히면 클라우드에 안 보낸다)
BLOCK_PRIORITY = ("risky", "needs_raw", "refused", "off_topic",
                  "confidential", "pii_residual",
                  "local_error", "cloud_error", "low_confidence")
DEFAULT_BLOCK_MESSAGE = "요청을 처리할 수 없습니다."


@dataclass
class Decision:
    route: Route
    # 이 경로를 고른 이유 코드. 차단이면 걸린 차단 사유 전부(우선순위 순)
    # 차단이 아닌 경로: faq, simple, complex, faq_unregistered, routing_refused, refused, cloud_error, low_confidence
    reasons: list[str] = field(default_factory=list)
    message: str | None = None    # 차단 안내문 또는 템플릿 답변. LLM을 부르는 경로는 None
    faq_id: str | None = None

    @property
    def reason(self) -> str:
        return self.reasons[0]


def match_faq(text: str, faq: FAQConfig) -> Decision | None:
    """등록된 FAQ와 맞으면 템플릿 답변. 판단 모델을 쓰지 않는 코드 확인이라 이 요청은 밖으로 나가지 않는다.

    잘못 맞히면 업무 요청에 엉뚱한 정해진 답이 나간다. 그래서 놓치는 쪽(→ 판단 → LLM)을 택하는 장치를 둔다:
    너무 긴 문장, 업무 요청 표현("써 줘", "작성" 등)이 있는 문장은 FAQ로 보지 않는다.
    """
    t = _norm(text)
    if len(text.strip()) > faq.max_chars or any(_norm(w) in t for w in faq.exclude_words):
        return None
    for entry in faq.entries:
        if any(all(_norm(k) in t for k in group) for group in entry.keywords):
            return Decision("template", ["faq"], entry.answer, entry.id)
    return None


def decide_after_local(local: JudgeResult, level: SecurityLevel, block_messages: dict[str, str]) -> Decision | None:
    """로컬 판단 뒤 차단할지 정한다. 차단하지 않으면 None (→ 클라우드 판단으로 넘어간다).

    - 기본 수준: 로컬 판단으로 막지 않는다. 남은 개인정보 의심의 처리는 M2 가명화 뒤에 정한다
    - 엄격 수준: 기밀 → 차단, 남은 개인정보 의심 → 차단. 로컬 판단이 실패하면 확인할 수 없으므로 차단
    - 완전 로컬 수준: 로컬 LLM 경로는 11/18 이후 선택 기능이라, 그 전까지 엄격 수준처럼 동작한다
    """
    if level == "basic":
        return None
    if not local.available:
        return _block(["local_error"], block_messages)
    j = local.judgments
    reasons = [reason for qid, reason in (("is_confidential", "confidential"), ("has_pii", "pii_residual"))
               if _yes(j.get(qid))]
    if any(_refused(j.get(q)) for q in ("is_confidential", "has_pii")):
        reasons.append("refused")
    return _block(reasons, block_messages) if reasons else None


def decide_after_cloud(cloud: JudgeResult, cfg: AppConfig) -> Decision:
    """클라우드 판단 뒤 경로를 정한다 (D12, D16).

    1. 판단 실패 → on_error_route (기본 저렴한 모델)
    2. 차단 사유를 모두 모은다: 위험, 원본 필요, 위험·원본 필요 판단 거부(on_refusal=block), 무관
    3. 난이도·의도 판단 거부 → on_routing_refusal (기본 고성능, "애매하면 고성능")
    4. 난이도·의도 신뢰도가 min_confidence 미만 → low_confidence_route (기본 고성능)
    5. 복잡 → 고성능, 나머지 → 저렴. 의도가 FAQ인데 등록된 답이 없으면 저렴한 모델이 답한다
    """
    c = cfg.decision.cloud
    msgs = cfg.routing.block_messages
    if not cloud.available:
        return _go(c.on_error_route, ["cloud_error"], msgs)

    j = cloud.judgments
    intent = j.get("intent")
    safety_refused = any(_refused(j.get(q)) for q in ("is_risky", "needs_raw_value"))

    blocks = [reason for qid, reason in (("is_risky", "risky"), ("needs_raw_value", "needs_raw")) if _yes(j.get(qid))]
    if safety_refused and c.on_refusal == "block":
        blocks.append("refused")
    # 무관 판단이 애매하면(신뢰도 미달) 막지 않는다. 정상 요청을 막는 쪽보다 고성능으로 보내는 쪽이 낫다
    if intent and intent.value == "off_topic" and _confident(intent, cfg.routing.min_confidence):
        blocks.append("off_topic")
    if blocks:
        return _block(blocks, msgs)

    if safety_refused:
        return _go(c.on_refusal, ["refused"], msgs)
    if any(_refused(j.get(q)) for q in ROUTING_QUESTIONS):
        return _go(c.on_routing_refusal, ["routing_refused"], msgs)
    if not all(_confident(j.get(q), cfg.routing.min_confidence) for q in ROUTING_QUESTIONS):
        return _go(cfg.routing.low_confidence_route, ["low_confidence"], msgs)

    difficulty = j.get("difficulty")
    if difficulty and difficulty.value == "complex":
        return Decision("premium", ["complex"])
    if intent and intent.value == "faq":
        return Decision("cheap", ["faq_unregistered"])
    return Decision("cheap", ["simple"])


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _yes(j: Judgment | None) -> bool:
    return j is not None and j.value is True


def _refused(j: Judgment | None) -> bool:
    return j is not None and j.refused


def _confident(j: Judgment | None, min_confidence: float | None) -> bool:
    """기준이 없거나(null), 질문을 묻지 않았거나, 신뢰도가 기준 이상이면 True."""
    return min_confidence is None or j is None or j.confidence is None or j.confidence >= min_confidence


def _block(reasons: list[str], messages: dict[str, str]) -> Decision:
    reasons = sorted(set(reasons), key=BLOCK_PRIORITY.index)
    return Decision("block", reasons, messages.get(reasons[0], DEFAULT_BLOCK_MESSAGE))


def _go(route: Route, reasons: list[str], messages: dict[str, str]) -> Decision:
    """설정이 고른 경로로 보낸다. 설정이 block이면 그 사유의 안내문을 붙인다 (난이도·의도 거부는 '판단 거부' 안내)."""
    if route != "block":
        return Decision(route, reasons)
    return _block(["refused" if r == "routing_refused" else r for r in reasons], messages)
