"""판단 서비스: 질문을 엔진에 나눠 보내고 기준값을 적용해 판단 결과를 만든다 (D12).

- judge_local(원문): 원본을 봐야 하는 판단(남은 개인정보, 기밀). 로컬 엔진에만 보낸다
- judge_cloud(가명화문, has_masked_pii): 가명화된 문장으로 충분한 판단.
  가명화와 로컬 판단이 끝난 뒤에, 차단되지 않은 요청만 부른다 (설계 결정 1번)
경로 결정(FAQ·저렴·고성능·차단)은 하지 않는다 → gateway/routing (#10)
엔진 오류는 예외 대신 JudgeResult.error로 돌려준다. 라우팅이 on_error_route 등으로 처리한다.
"""
from dataclasses import dataclass, field

from gateway.config import DecisionConfig, Settings
from gateway.decision.engines import (DecisionError, Engine, EngineAnswer, OpenAIDecisionsEngine,
                                      SystemOneEngine)
from gateway.decision.questions import INTENT_GROUP, QUESTIONS, RETRY_ON_REFUSAL


@dataclass
class Judgment:
    question: str
    probs: dict[str, float] | None   # 엔진이 준 확률표. 거부면 None
    # 예/아니오 판단: 기준값 이상이면 True / 난이도: "simple"|"complex" / 의도: "faq"|"work"|"off_topic"
    value: bool | str | None
    confidence: float | None         # (K·p_max − 1)/(K − 1), 코드에서 계산. 로그·화면 표시용
    retried: bool = False            # 거부되어 한 번 더 물었는가

    @property
    def refused(self) -> bool:
        return self.probs is None


@dataclass
class JudgeResult:
    engine: str                      # "local" | "cloud"
    model: str                       # 요청한 모델 이름 (응답의 model은 서버마다 다르다)
    judgments: dict[str, Judgment] = field(default_factory=dict)
    latency_ms: float = 0.0          # 다시 물은 시간 포함
    input_tokens: int = 0
    error: str | None = None         # 엔진 오류(시간 초과 등). 이때 judgments는 비어 있다

    @property
    def available(self) -> bool:
        return self.error is None


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

    def judge_local(self, text: str) -> JudgeResult:
        return self._ask("local", self.local, text, self.cfg.local_questions)

    def judge_cloud(self, masked_text: str, has_masked_pii: bool) -> JudgeResult:
        qids = [q for q in self.cfg.cloud.questions
                if not (q == "needs_raw_value" and self.cfg.cloud.needs_raw_only_if_masked and not has_masked_pii)]
        result = self._ask("cloud", self.cloud, masked_text, qids)

        # 거부된 라우팅 질문만 따로 한 번 더 묻는다 (D14: 거부로 막히는 정상 요청 ≤ 1%)
        refused = [q for q, j in result.judgments.items() if j.refused and q in RETRY_ON_REFUSAL]
        if refused:
            retry = self._ask("cloud", self.cloud, masked_text, refused)
            result.latency_ms += retry.latency_ms
            result.input_tokens += retry.input_tokens
            for q in refused:
                again = retry.judgments.get(q)  # 다시 묻다가 오류가 나면 처음 거부를 그대로 둔다
                result.judgments[q] = again if again is not None else result.judgments[q]
                result.judgments[q].retried = True
        return result

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
