"""판단 서비스: 질문을 엔진에 나눠 보내고 기준값을 적용해 판단 결과를 만든다 (D12).

- judge_local(메시지들): 원본을 봐야 하는 판단(기밀). 로컬 엔진에만 보낸다.
  판단 모델이 읽을 수 있는 길이에 맞춰 구간으로 나눠 묻고, 같은 구간은 기억한 결과를 쓴다 (#71, chunks.py)
- judge_cloud(가명화문, has_masked_pii): 가명화된 문장으로 충분한 판단.
  가명화와 로컬 판단이 끝난 뒤에, 차단되지 않은 요청만 부른다 (설계 결정 1번)
경로 결정(FAQ·저렴·고성능·차단)은 하지 않는다 → gateway/routing (#10)
판단 거부는 refused로 표시만 하고 다시 묻지 않는다 (다시 물어도 결과가 같았다, #8).
엔진 오류는 예외 대신 JudgeResult.error로 돌려준다. 라우팅이 on_error_route 등으로 처리한다.
"""
import hashlib
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

from gateway.config import DecisionConfig, Settings
from gateway.decision.chunks import chunks, halves
from gateway.decision.engines import (TOO_LONG, DecisionError, Engine, EngineAnswer, InputTooLong,
                                      OpenAIDecisionsEngine, SystemOneEngine, systemone_questions)
from gateway.decision.questions import INTENT_GROUP, QUESTIONS


@dataclass
class Judgment:
    question: str
    probs: dict[str, float] | None   # 엔진이 준 확률표. 거부면 None
    # 예/아니오 판단: 기준값 이상이면 True / 난이도: "simple"|"complex" / 의도: "faq"|"work"|"off_topic"
    value: bool | str | None
    confidence: float | None         # (K·p_max − 1)/(K − 1), 코드에서 계산. 로그·화면 표시용

    @property
    def refused(self) -> bool:
        return self.probs is None


@dataclass
class JudgeResult:
    engine: str                      # "local" | "cloud"
    model: str                       # 요청한 모델 이름 (응답의 model은 서버마다 다르다)
    judgments: dict[str, Judgment] = field(default_factory=dict)
    latency_ms: float = 0.0
    input_tokens: int = 0
    error: str | None = None         # 엔진 오류(시간 초과 등). 이때 judgments는 비어 있다
    cached: bool = False             # 엔진을 부르지 않고 기억해 둔 결과를 썼다 (이전 대화 위험 판단, 로컬 판단의 모든 구간)
    parts: int = 1                   # 로컬 판단: 나눠 물은 구간 수 (#71)
    cached_parts: int = 0            # 로컬 판단: 그중 기억한 결과를 쓴 구간 수

    @property
    def available(self) -> bool:
        return self.error is None


def _merge(answers: list[dict[str, EngineAnswer]], qs: dict) -> dict[str, EngineAnswer]:
    """구간별 답을 하나로. 거부가 있으면 거부, 예/아니오는 가장 높은 '예' 확률, 그 밖은 마지막 구간."""
    out = {}
    for q, spec in qs.items():
        got = [a[q] for a in answers if q in a]
        if not got or any(a.refused for a in got):
            out[q] = EngineAnswer(None)
        elif spec.type == "noul":
            out[q] = max(got, key=lambda a: a.probs["yes"])
        else:
            out[q] = got[-1]
    return out


def confidence(probs: dict[str, float]) -> float:
    """TypeSafe 공식. 예/아니오는 {"yes": p}뿐이므로 K=2, p_max=max(p, 1−p)."""
    ps = [probs["yes"], 1 - probs["yes"]] if list(probs) == ["yes"] else list(probs.values())
    k = len(ps)
    return round((k * max(ps) - 1) / (k - 1), 4)


class DecisionService:
    def __init__(self, local: Engine, cloud: Engine, cfg: DecisionConfig):
        self.local = local
        self.cloud = cloud
        self.cfg = cfg
        # 로컬 판단 구간 → 질문별 답. 판단 모델은 같은 글에 늘 같은 답을 낸다(11/08 확인, #71)
        # 키는 해시라 글 자체는 남지 않는다. 판단 모델·질문 문장이 바뀌면 키도 바뀐다
        self._local_cache: OrderedDict[str, dict[str, EngineAnswer]] = OrderedDict()
        self._lock = threading.Lock()                  # FastAPI는 요청을 여러 스레드에서 처리한다

    @classmethod
    def from_settings(cls, settings: Settings, transport=None) -> "DecisionService":
        d = settings.config.decision
        key = settings.secrets.openai_api_key
        if key is None or not key.get_secret_value():
            raise ValueError(".env에 OPENAI_API_KEY가 없다 (클라우드 판단에 필요)")
        local = SystemOneEngine(d.base_url, d.model, d.timeout_seconds, transport=transport)
        cloud = OpenAIDecisionsEngine(d.cloud.base_url, d.cloud.model, d.cloud.timeout_seconds,
                                      api_key=key.get_secret_value(), transport=transport)
        return cls(local, cloud, d)

    def close(self) -> None:
        self.local.close()
        self.cloud.close()

    def judge_local(self, texts: str | list[str], questions: list[str] | None = None) -> JudgeResult:
        """원본을 봐야 하는 판단. texts는 메시지 내용들(문자열 하나도 된다). questions를 주지 않으면 설정대로 묻는다.

        판단 모델이 읽을 수 있는 길이(#39)에 맞춰 구간으로 나눠 묻는다 (#71, chunks.py)
        - 예/아니오 질문은 한 구간이라도 '예'면 '예'다: 구간 중 가장 높은 확률을 쓴다
        - 그 밖의 질문(설정으로 넣었을 때)은 마지막 구간(이번 질문이 든 곳)의 답을 쓴다
        - 한 구간이라도 판단 거부면 거부, 실패하면 전체 실패다 (엄격 수준은 막는다, D19)
        - 같은 구간은 기억한 결과를 쓴다. 새로 물을 구간이 local_max_new_chunks를 넘으면 부르지 않고 실패로 돌려준다
          (긴 도구 결과 등으로 지연이 끝없이 늘지 않게)"""
        qids = self.cfg.local_questions if questions is None else questions
        if not qids:
            return JudgeResult("local", self.local.model)
        qs = {q: QUESTIONS[q] for q in qids}
        parts = chunks([texts] if isinstance(texts, str) else list(texts),
                       self.cfg.local_chunk_chars, self.cfg.local_chunk_overlap)
        keys = [self._local_key(qs, p) for p in parts]
        with self._lock:
            known = {k: self._local_cache[k] for k in keys if k in self._local_cache}
            for k in known:
                self._local_cache.move_to_end(k)
        cached_parts = sum(k in known for k in keys)
        new = len({k for k in keys if k not in known})
        if new > self.cfg.local_max_new_chunks:
            return JudgeResult("local", self.local.model, parts=len(parts), cached_parts=cached_parts,
                               error=f"{TOO_LONG}: 새로 판단할 구간 {new}개 (상한 {self.cfg.local_max_new_chunks}개)")
        answers, latency, tokens = [], 0.0, 0
        budget = [self.cfg.local_max_calls]     # 이 요청에서 남은 판단 호출 수. 반으로 나눠 다시 묻는 것도 여기서 뺀다
        try:
            for part, key in zip(parts, keys):
                if key not in known:
                    known[key], ms, n = self._ask_part(part, qs, 0, budget)
                    latency, tokens = latency + ms, tokens + n
                    self._remember(key, known[key])
                answers.append(known[key])
        except DecisionError as e:
            return JudgeResult("local", self.local.model, error=str(e), latency_ms=latency, input_tokens=tokens,
                               parts=len(parts), cached_parts=cached_parts)
        merged = _merge(answers, qs)
        return JudgeResult("local", self.local.model, {q: self._judgment(q, a) for q, a in merged.items()},
                           latency, tokens, cached=cached_parts == len(parts), parts=len(parts), cached_parts=cached_parts)

    def _ask_part(self, text: str, qs: dict, depth: int, budget: list[int]) -> tuple[dict[str, EngineAnswer], float, int]:
        """구간 하나. 판단 모델이 그래도 길다고 하면(글자당 토큰이 많은 글) 반으로 나눠 다시 묻는다 (세 번까지).
        호출마다 요청의 예산(local_max_calls)에서 뺀다. 예산이 없으면 실패다: 다시 묻기가 구간마다 최대 15번이라,
        글자당 토큰이 많은 글로 상한(새 구간 8개)을 넘는 호출을 만들 수 없게 한다 (11/08 보안 검토)."""
        if budget[0] <= 0:
            raise DecisionError(f"{TOO_LONG}: 판단 호출 상한 {self.cfg.local_max_calls}번을 넘음")
        budget[0] -= 1
        try:
            r = self.local.decide(text, qs)
            return r.answers, r.latency_ms, r.input_tokens
        except InputTooLong:
            if depth >= 3:
                raise
        results = [self._ask_part(h, qs, depth + 1, budget) for h in halves(text)]
        return (_merge([a for a, _, _ in results], qs), sum(ms for _, ms, _ in results),
                sum(n for _, _, n in results))

    def _local_key(self, qs: dict, text: str) -> str:
        spec = json.dumps(systemone_questions(qs), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256("\x00".join([self.local.model, spec, text]).encode("utf-8")).hexdigest()

    def _remember(self, key: str, answers: dict[str, EngineAnswer]) -> None:
        with self._lock:
            self._local_cache[key] = answers
            while len(self._local_cache) > self.cfg.local_cache_size:
                self._local_cache.popitem(last=False)

    def judge_cloud(self, masked_text: str, has_masked_pii: bool) -> JudgeResult:
        qids = [q for q in self.cfg.cloud.questions
                if not (q == "needs_raw_value" and self.cfg.cloud.needs_raw_only_if_masked and not has_masked_pii)]
        return self._ask("cloud", self.cloud, masked_text, qids)

    def judge_history_risk(self, masked_text: str) -> JudgeResult:
        """마지막 사용자 메시지 밖의 내용(이전 대화·도구 결과·이전 답변)에 인젝션이 숨어 있는지 위험 질문만 묻는다.
        LLM에는 대화 전체가 가므로, 마지막 메시지만 판단하면 앞쪽에 숨긴 지시를 놓친다 (#12 보안 검토)."""
        qids = [q for q in self.cfg.cloud.questions if q == "is_risky"]
        return self._ask("cloud_history", self.cloud, masked_text, qids)

    def _ask(self, name: str, engine: Engine, text: str, qids: list[str]) -> JudgeResult:
        if not qids:
            return JudgeResult(name, engine.model)
        try:
            r = engine.decide(text, {q: QUESTIONS[q] for q in qids})
        except DecisionError as e:
            return JudgeResult(name, engine.model, error=str(e))
        judgments = {q: self._judgment(q, a) for q, a in r.answers.items()}
        return JudgeResult(name, engine.model, judgments, r.latency_ms, r.input_tokens)

    def _judgment(self, qid: str, answer: EngineAnswer) -> Judgment:
        probs = answer.probs
        if probs is None:
            return Judgment(qid, None, None, None)
        th = self.cfg.thresholds
        if qid == "difficulty":
            value = "complex" if probs["complex"] >= th.get("difficulty_complex", 0.5) else "simple"
        elif qid == "intent":
            value = INTENT_GROUP[max(probs, key=probs.get)]
        else:
            value = probs["yes"] >= th.get(qid, 0.5)
        return Judgment(qid, probs, value, confidence(probs))
