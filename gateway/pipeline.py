"""요청 처리 흐름 (#12): 가명화 → FAQ → 로컬 판단 → 클라우드 판단 → LLM → 복원 → 로그.

순서는 D12를 따른다. 단계별 규칙은 각 모듈에 있고, 이 파일은 순서와 기록만 맡는다.
- 가명화 부품(Masker)은 바꿔 끼울 수 있다. M1은 UnavailableMasker라서, 개인정보가 보이거나 확인할 수 없는
  요청은 밖으로 보내지 않고 막는다(masking_unavailable). M2에서 진짜 가명화로 바꾼다
- FAQ와 클라우드 판단은 마지막 사용자 메시지로 한다. 로컬 판단(남은 개인정보·기밀)은 밖으로 나갈 메시지 전체로 한다.
  LLM에는 대화 전체가 가므로 그 전부를 봐야 한다
- 모든 경로가 요청 로그를 남긴다. 로그 저장이 실패해도 응답은 돌려준다. 로그에는 가명화된 문장만 들어간다 (#11)
"""
import logging
import time
from dataclasses import dataclass, field

from gateway.config import Settings
from gateway.decision.service import DecisionService, JudgeResult
from gateway.llm.client import LLMClient, LLMError, LLMResult, LLMUsage
from gateway.llm.cost import baseline_cost, request_cost
from gateway.masking.masker import Masker, UnavailableMasker
from gateway.routing.policy import (Decision, apply_history_risk, decide_after_cloud, decide_after_local,
                                    decide_without_masking, match_faq)
from gateway.storage.log import RequestLog, RequestRecord, judge_summary

logger = logging.getLogger(__name__)


@dataclass
class GatewayReply:
    request_id: str
    route: str                    # template | cheap | premium | block
    text: str                     # 사용자에게 돌려줄 답 (복원된 LLM 답변, 템플릿 답변, 차단 안내문)
    finish_reason: str            # stop | content_filter(차단) | LLM이 준 값
    model: str | None = None      # 실제로 부른 LLM 모델. 템플릿·차단이면 None
    usage: LLMUsage | None = None
    refusal: str | None = None    # LLM이 답변을 거부한 문구
    error: str | None = None      # LLM 오류 (API는 502로 돌려준다)


@dataclass
class _Trace:
    """한 요청에서 모은 것. 끝에 로그 한 줄이 된다."""
    request: str                                    # 가명화된 마지막 사용자 메시지
    pii_counts: dict[str, int]
    judges: list[JudgeResult] = field(default_factory=list)


class Gateway:
    def __init__(self, settings: Settings, decision: DecisionService, llm: LLMClient, log: RequestLog | None,
                 masker: Masker | None = None):
        self.settings = settings
        self.decision = decision
        self.llm = llm
        self.log = log
        self.masker = masker or UnavailableMasker()

    @classmethod
    def from_settings(cls, settings: Settings, transport=None, masker: Masker | None = None,
                      log: RequestLog | None = None) -> "Gateway":
        return cls(settings, DecisionService.from_settings(settings, transport),
                   LLMClient.from_settings(settings, transport),
                   log if log is not None else RequestLog.from_config(settings.config.storage), masker)

    def close(self) -> None:
        self.decision.close()
        self.llm.close()

    def handle(self, messages: list[dict[str, str]], max_completion_tokens: int | None = None) -> GatewayReply:
        t0 = time.perf_counter()
        cfg = self.settings.config
        msgs = cfg.routing.block_messages
        masked = self.masker.mask(messages)
        trace = _Trace(_last_user(masked.messages), masked.counts)

        # ① FAQ: 판단 전 코드 확인. 맞으면 아무것도 밖으로 보내지 않는다
        decision = match_faq(trace.request, cfg.faq)
        # ② 로컬 판단: 원본을 봐야 하는 판단 (남은 개인정보·기밀). 차단이면 클라우드로 보내지 않는다
        if decision is None:
            local = self.decision.judge_local("\n".join(m["content"] for m in masked.messages if m["content"]))
            trace.judges.append(local)
            if not self.masker.available:
                decision = decide_without_masking(masked.has_pii, local, msgs)
            decision = decision or decide_after_local(local, cfg.security_level, msgs)
        # ③ 클라우드 판단: 가명화된 문장으로 충분한 판단 (원본 필요·위험·난이도·의도)
        if decision is None:
            cloud = self.decision.judge_cloud(trace.request, has_masked_pii=masked.has_pii)
            trace.judges.append(cloud)
            decision = decide_after_cloud(cloud, cfg)
            # LLM에는 대화 전체가 간다. 앞쪽 대화·도구 결과에 숨긴 인젝션도 위험 판단을 거친다 (단발 요청은 추가 호출 없음)
            history = _untrusted_history(masked.messages)
            if history and decision.route != "block":
                hist = self.decision.judge_history_risk(history)
                trace.judges.append(hist)
                decision = apply_history_risk(decision, hist, cfg)

        if decision.route == "template":
            return self._finish(trace, decision, t0, decision.message, "stop")
        if decision.route == "block":
            return self._finish(trace, decision, t0, decision.message, "content_filter")

        # ④ LLM: 가명화된 대화 전체를 보낸다. 가명화된 개인정보가 있으면 "토큰을 그대로 써라" 지시를 붙인다 (D17)
        try:
            result = self.llm.complete(decision.route, masked.messages, masked=masked.has_pii,
                                       max_completion_tokens=max_completion_tokens)
        except LLMError as e:
            return self._finish(trace, decision, t0, "", "stop", error=str(e))
        text = self.masker.restore(result.text, masked.mapping)
        return self._finish(trace, decision, t0, text, result.finish_reason, result=result)

    def _finish(self, trace: _Trace, decision: Decision, t0: float, text: str | None, finish_reason: str,
                result: LLMResult | None = None, error: str | None = None) -> GatewayReply:
        llm_cfg = self.settings.config.llm
        usage = result.usage if result else None
        cost = request_cost(usage, llm_cfg.cheap if result.tier == "cheap" else llm_cfg.premium) if result else 0.0
        # LLM이 실패하면 쓴 토큰도 아낀 비용도 없다. 템플릿·차단은 cost.py 규칙대로 (D18)
        baseline = 0.0 if error else baseline_cost(decision.route, usage, trace.request, llm_cfg)
        record = RequestRecord(
            security_level=self.settings.config.security_level, text_masked=trace.request,
            route=decision.route, reasons=decision.reasons, pii_counts=trace.pii_counts, faq_id=decision.faq_id,
            judgments={j.engine: judge_summary(j) for j in trace.judges},
            tier=result.tier if result else None, model=result.model if result else None,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            reasoning_tokens=usage.reasoning_tokens if usage else 0,
            cost_usd=cost, baseline_cost_usd=baseline,
            decision_input_tokens=sum(j.input_tokens for j in trace.judges if j.engine.startswith("cloud")),
            decision_latency_ms=round(sum(j.latency_ms for j in trace.judges), 1),
            total_latency_ms=round((time.perf_counter() - t0) * 1000, 1), llm_error=error)
        self._save(record)
        return GatewayReply(record.id, decision.route, text or "", finish_reason,
                            model=result.model if result else None, usage=usage,
                            refusal=result.refusal if result else None, error=error)

    def _save(self, record: RequestRecord) -> None:
        if self.log is None:
            return
        try:
            self.log.save(record)
        except Exception as e:   # 로그 때문에 응답이 실패하면 안 된다. 메시지에 요청 내용을 넣지 않는다
            logger.error("요청 로그 저장 실패 (%s): %s", record.id, type(e).__name__)


def _last_user_index(messages: list[dict[str, str]]) -> int:
    for i in range(len(messages) - 1, -1, -1):
        if messages[i]["role"] == "user":
            return i
    return len(messages) - 1


def _last_user(messages: list[dict[str, str]]) -> str:
    """판단할 요청 문장: 마지막 사용자 메시지. 없으면 마지막 메시지."""
    return messages[_last_user_index(messages)]["content"]


def _untrusted_history(messages: list[dict[str, str]]) -> str:
    """마지막 사용자 메시지를 뺀 나머지 중 회사가 쓰지 않은 내용: 이전 사용자 대화, 도구 결과, 이전 답변.
    system·developer는 회사가 쓴 지시문이라 뺀다. 거기 있는 '보안' 같은 말이 오탐을 내면 모든 요청이 막힌다."""
    last = _last_user_index(messages)
    return "\n".join(m["content"] for i, m in enumerate(messages)
                     if i != last and m["role"] not in ("system", "developer") and m["content"])
