"""요청 처리 흐름 (#12): 가명화 → FAQ → 로컬 판단 → 클라우드 판단 → LLM → 복원 → 로그.

순서는 D12를 따른다. 단계별 규칙은 각 모듈에 있고, 이 파일은 순서와 기록만 맡는다.
- 가명화 부품(Masker)은 바꿔 끼울 수 있다. 기본은 규칙 가명화 RuleMasker(사용자 사전 + 정규식, #44).
  가명화를 할 수 없는 부품(UnavailableMasker)이면 개인정보가 보이거나 확인할 수 없는 요청을 막는다(masking_unavailable)
- 사전에 없는 이름은 가명화 부품 안의 이름 인식 모델이 가린다(D23). 그 모델이 본 주소가 가린 뒤에도 남았으면 엄격 수준은 막는다
- FAQ와 클라우드 판단은 마지막 사용자 메시지로 한다. 로컬 판단(기밀)은 밖으로 나갈 메시지 전체로 한다.
  LLM에는 대화 전체가 가므로 그 전부를 봐야 한다
- 모든 경로가 요청 로그를 남긴다. 로그 저장이 실패해도 응답은 돌려준다. 로그에는 가명화된 문장만 들어간다 (#11)
"""
import hashlib
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace

from gateway.config import Settings
from gateway.decision.questions import QUESTIONS
from gateway.decision.service import DecisionService, JudgeResult
from gateway.llm.client import LLMClient, LLMError, LLMResult, LLMUsage
from gateway.llm.cost import baseline_cost, request_cost
from gateway.masking.masker import Masker, RuleMasker, UnavailableMasker
from gateway.routing.policy import (Decision, apply_history_risk, decide_after_cloud, decide_after_local,
                                    decide_suspected, decide_unmasked, decide_without_masking, match_faq)
from gateway.storage.log import RequestLog, RequestRecord, judge_summary

logger = logging.getLogger(__name__)
HISTORY_CACHE_SIZE = 256      # 기억할 이전 대화 위험 판단 수
HISTORY_CACHE_TTL = 600       # 초. 지나면 다시 묻는다
HISTORY_SAFE_MARGIN = 0.2     # 위험 확률이 기준값의 20% 이하여야 "확실히 안전"으로 기억한다


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
    withhold: bool = False                          # 가리지 못한 개인정보가 남아 로그에 문장을 남기지 않는다
    judges: list[JudgeResult] = field(default_factory=list)
    unrestored: int = 0                             # 답변에서 복원하지 못한 토큰 수 (#45)


class Gateway:
    def __init__(self, settings: Settings, decision: DecisionService, llm: LLMClient, log: RequestLog | None,
                 masker: Masker | None = None):
        self.settings = settings
        self.decision = decision
        self.llm = llm
        self.log = log
        self.masker = masker or UnavailableMasker()
        self._history_cache: OrderedDict[str, tuple[float, JudgeResult]] = OrderedDict()   # 해시 → (만료 시각, 판단)
        self._cache_lock = threading.Lock()                                  # FastAPI는 요청을 여러 스레드에서 처리한다

    @classmethod
    def from_settings(cls, settings: Settings, transport=None, masker: Masker | None = None,
                      log: RequestLog | None = None) -> "Gateway":
        return cls(settings, DecisionService.from_settings(settings, transport),
                   LLMClient.from_settings(settings, transport),
                   log if log is not None else RequestLog.from_config(settings.config.storage),
                   masker or RuleMasker.from_config(settings.config.masking))

    def close(self) -> None:
        self.decision.close()
        self.llm.close()

    def handle(self, messages: list[dict[str, str]], max_completion_tokens: int | None = None) -> GatewayReply:
        t0 = time.perf_counter()
        cfg = self.settings.config
        msgs = cfg.routing.block_messages
        masked = self.masker.mask(messages)
        # 가리지 못한 개인정보가 남았거나 남았을 수 있으면(주소) 로그에 문장을 남기지 않는다
        trace = _Trace(_last_user(masked.messages), masked.counts, withhold=bool(masked.unmasked or masked.suspected))

        # ① FAQ: 판단 전 코드 확인. 맞으면 아무것도 밖으로 보내지 않는다
        decision = match_faq(trace.request, cfg.faq)
        # 가린 뒤에도 형식 있는 개인정보가 남았으면(정규화로도 못 맞춘 표기) 아무 판단 전에 막는다
        decision = decision or decide_unmasked(masked.unmasked, msgs)
        # 이름 인식 모델이 본 주소가 가린 뒤에도 남았으면 엄격 수준은 막는다 (D23)
        decision = decision or decide_suspected(masked.suspected, cfg.security_level, msgs)
        # ② 로컬 판단: 원본을 봐야 하는 판단 (기밀). 차단이면 클라우드로 보내지 않는다
        if decision is None:
            questions = cfg.decision.local_questions
            if not self.masker.available and "has_pii" not in questions:
                questions = [*questions, "has_pii"]      # 가명화 부품이 없으면(M1) 개인정보가 있는지 물어 막는다
            # 메시지마다 넘긴다: 판단 서비스가 판단 모델 길이에 맞춰 구간으로 나누고, 반복되는 구간은 기억한다 (#71)
            # system·developer 지시문은 기본으로 넣지 않는다(local_check_system_messages, D19 보완). 가명화와
            # 위험 판단(이전 대화·지시문 포함)은 그대로 전부 거친다
            skip = () if cfg.decision.local_check_system_messages else ("system", "developer")
            local = self.decision.judge_local(
                [m["content"] for m in masked.messages if m["content"] and m["role"] not in skip], questions)
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
            history = _untrusted_history(masked.messages, cfg.decision.cloud.check_system_messages)
            if history and decision.route != "block":
                hist = self._judge_history(history)
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
        restored = self.masker.restore(result.text, masked.mapping)
        trace.unrestored = restored.unrestored
        return self._finish(trace, decision, t0, restored.text, result.finish_reason, result=result)

    def _judge_history(self, text: str) -> JudgeResult:
        """이전 대화 위험 판단. 챗봇은 매번 같은 system 지시문을 보내므로, 같은 내용은 결과를 기억해 다시 묻지 않는다.

        판단은 확률이라 같은 문장도 결과가 조금씩 다르다. 아무 결과나 기억하면 공격자가 같은 인젝션을 여러 번 보내다
        한 번 나온 "안전" 판정을 고정시킬 수 있다(보안 검토: 캐시 오염). 그래서
        - 확실한 판정만 기억한다: 위험 확률이 기준값 이상(차단)이거나 기준값 × HISTORY_SAFE_MARGIN 이하(확실히 안전)
        - HISTORY_CACHE_TTL초가 지나면 잊는다
        - 키에 판단 모델·기준값·질문 문장을 넣어, 설정이 바뀌면 예전 결과를 쓰지 않는다
        기억한 결과를 쓸 때는 cached=True, 지연·토큰 0으로 남긴다 (실제로 부르지 않았다)."""
        th = self.settings.config.decision.thresholds.get("is_risky", 0.5)
        key = hashlib.sha256("\x00".join([self.settings.config.decision.cloud.model, str(th),
                                          QUESTIONS["is_risky"].instructions, text]).encode("utf-8")).hexdigest()
        now = time.monotonic()
        with self._cache_lock:
            entry = self._history_cache.get(key)
            if entry is not None and entry[0] <= now:     # 만료
                del self._history_cache[key]
                entry = None
            if entry is not None:
                self._history_cache.move_to_end(key)
        if entry is not None:
            return replace(entry[1], latency_ms=0.0, input_tokens=0, cached=True)
        result = self.decision.judge_history_risk(text)
        j = result.judgments.get("is_risky")
        if result.available and j is not None and j.probs is not None and (
                j.probs["yes"] >= th or j.probs["yes"] <= th * HISTORY_SAFE_MARGIN):
            with self._cache_lock:
                self._history_cache[key] = (now + HISTORY_CACHE_TTL, result)
                if len(self._history_cache) > HISTORY_CACHE_SIZE:
                    self._history_cache.popitem(last=False)
        return result

    def _finish(self, trace: _Trace, decision: Decision, t0: float, text: str | None, finish_reason: str,
                result: LLMResult | None = None, error: str | None = None) -> GatewayReply:
        llm_cfg = self.settings.config.llm
        usage = result.usage if result else None
        cost = request_cost(usage, llm_cfg.cheap if result.tier == "cheap" else llm_cfg.premium) if result else 0.0
        # LLM이 실패하면 쓴 토큰도 아낀 비용도 없다. 템플릿·차단은 cost.py 규칙대로 (D18)
        baseline = 0.0 if error else baseline_cost(decision.route, usage, trace.request, llm_cfg)
        record = RequestRecord(
            security_level=self.settings.config.security_level, text_masked=trace.request, withhold_text=trace.withhold,
            route=decision.route, reasons=decision.reasons, pii_counts=trace.pii_counts, faq_id=decision.faq_id,
            judgments={j.engine: judge_summary(j) for j in trace.judges},
            tier=result.tier if result else None, model=result.model if result else None,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            reasoning_tokens=usage.reasoning_tokens if usage else 0,
            cost_usd=cost, baseline_cost_usd=baseline,
            decision_input_tokens=sum(j.input_tokens for j in trace.judges if j.engine.startswith("cloud")),
            decision_latency_ms=round(sum(j.latency_ms for j in trace.judges), 1),
            total_latency_ms=round((time.perf_counter() - t0) * 1000, 1), llm_error=error,
            unrestored_tokens=trace.unrestored)
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


def _untrusted_history(messages: list[dict[str, str]], include_system: bool = True) -> str:
    """마지막 사용자 메시지를 뺀 나머지: 이전 사용자 대화, 도구 결과, 이전 답변, system·developer 지시문.
    system·developer도 클라이언트가 보내는 값이라 기본으로 넣는다. 직원이 직접 쓰는 도구라면 마음대로 바꿀 수 있다.
    회사가 모든 클라이언트를 통제할 때만 include_system=False(설정 check_system_messages)로 뺀다."""
    last = _last_user_index(messages)
    skip = () if include_system else ("system", "developer")
    return "\n".join(m["content"] for i, m in enumerate(messages)
                     if i != last and m["role"] not in skip and m["content"])
