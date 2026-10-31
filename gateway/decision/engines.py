"""판단 엔진 클라이언트 (설계 결정 5번).

- SystemOneEngine: /v1/systemone 형식 (Ollaya, laya-serve, Jev). 로컬 판단에 쓴다
- OpenAIDecisionsEngine: OpenAI Decisions(/v1/decisions) 어댑터. 가명화된 문장만 받는다
두 엔진 모두 질문별 확률표(EngineAnswer)를 돌려준다. 응답의 confidence 필드는 서버마다 계산법이 달라서
쓰지 않는다 (docs/laya-serve-notes.md). 형식 출처: docs/ollaya-notes.md, docs/decision-client.md
"""
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from gateway.decision.questions import Question


@dataclass
class EngineAnswer:
    # noul·predicate: {"yes": P(예)}, score·choice: {라벨: 확률}. 판단 거부(refusal)면 None
    probs: dict[str, float] | None

    @property
    def refused(self) -> bool:
        return self.probs is None


@dataclass
class EngineResult:
    answers: dict[str, EngineAnswer]
    latency_ms: float      # 왕복 시간 (네트워크 포함)
    input_tokens: int


class DecisionError(Exception):
    """엔진이 응답하지 않거나(시간 초과·연결 실패), 오류 응답이나 알 수 없는 형식을 돌려줌.

    메시지에 요청 문장이나 API 키를 넣지 않는다 (httpx 예외 메시지에는 URL과 상태 코드만 들어간다).
    """


class Engine(Protocol):
    model: str

    def decide(self, text: str, questions: dict[str, Question]) -> EngineResult: ...

    def close(self) -> None: ...


class _HttpEngine:
    """HTTP 연결을 재사용하는 공통 부분. 하위 클래스는 요청 본문과 응답 해석만 정한다."""

    path = ""

    def __init__(self, base_url: str, model: str, timeout: float, api_key: str,
                 transport: httpx.BaseTransport | None = None):
        self.model = model
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport,
                                    headers={"Authorization": f"Bearer {api_key}"})

    def decide(self, text: str, questions: dict[str, Question]) -> EngineResult:
        t0 = time.perf_counter()
        try:
            r = self._client.post(self.path, json=self._body(text, questions))
            r.raise_for_status()
            res = r.json()
            answers = self._parse(res, questions)
        except httpx.HTTPError as e:
            raise DecisionError(f"{type(self).__name__}: {e}") from e
        except (KeyError, TypeError, ValueError) as e:  # 문서와 다른 응답 형식
            raise DecisionError(f"{type(self).__name__}: 알 수 없는 응답 형식 ({type(e).__name__}: {e})") from e
        latency_ms = (time.perf_counter() - t0) * 1000
        return EngineResult(answers, latency_ms, int(res.get("usage", {}).get("input_tokens", 0)))

    def close(self) -> None:
        self._client.close()

    def _body(self, text: str, questions: dict[str, Question]) -> dict[str, Any]:
        raise NotImplementedError

    def _parse(self, res: dict, questions: dict[str, Question]) -> dict[str, EngineAnswer]:
        raise NotImplementedError


def systemone_questions(questions: dict[str, Question]) -> dict[str, dict[str, Any]]:
    """질문 정의 → /v1/systemone 질문 형식. 파인튜닝 학습 데이터(finetune/generate.py)도 이 함수를 써서
    학습 때와 추론 때 질문이 글자 하나까지 같게 한다."""
    qs = {}
    for qid, q in questions.items():
        body: dict[str, Any] = {"type": q.type, "instructions": q.instructions}
        if q.type == "choice":
            body["criteria"] = q.criteria
        elif q.type == "score":  # 레벨 설명 배열 (0부터)
            body["criteria"] = list(q.criteria.values())
        qs[qid] = body
    return qs


class SystemOneEngine(_HttpEngine):
    """/v1/systemone (TypeSafe 형식). Ollaya는 키가 없어도 되지만 형식상 아무 값이나 보낸다."""

    path = "/v1/systemone"

    def __init__(self, base_url: str, model: str, timeout: float, api_key: str = "local",
                 transport: httpx.BaseTransport | None = None):
        super().__init__(base_url, model, timeout, api_key, transport)

    def _body(self, text, questions):
        return {"model": self.model, "state": text, "questions": systemone_questions(questions)}

    def _parse(self, res, questions):
        out = {}
        for qid, q in questions.items():
            a = res["answers"][qid]
            if q.type == "noul":
                probs = {"yes": a["noul"]}
            elif q.type == "score":  # 확률표의 키가 "0", "1", … → 레벨 라벨로
                probs = {label: a["probabilities"][str(i)] for i, label in enumerate(q.criteria)}
            else:
                probs = dict(a["probabilities"])
            out[qid] = EngineAnswer(probs)
        return out


class OpenAIDecisionsEngine(_HttpEngine):
    """OpenAI Decisions 어댑터. noul → predicate, score의 레벨 → levels, choice의 선택지 → choices."""

    path = "/decisions"

    def _body(self, text, questions):
        qs = []
        for qid, q in questions.items():
            body: dict[str, Any] = {"name": qid, "instructions": q.instructions}
            if q.type == "noul":
                body["type"] = "predicate"
            elif q.type == "score":
                body |= {"type": "score",
                         "levels": [{"label": k, "description": v} for k, v in q.criteria.items()]}
            else:
                body |= {"type": "choice",
                         "choices": [{"value": k, "description": v} for k, v in q.criteria.items()]}
            qs.append(body)
        return {"model": self.model, "input": text, "questions": qs}

    def _parse(self, res, questions):
        out = {}
        # 답은 질문 순서대로 온다 (공식 문서). 이름이 있으면 순서가 맞는지 확인한다
        for (qid, _q), a in zip(questions.items(), res["answers"], strict=True):
            if a.get("name") not in (qid, None):
                raise ValueError(f"답의 순서가 질문과 다름: {a.get('name')} ≠ {qid}")
            if a["type"] == "refusal":  # 질문별 거부. 이유 필드는 없다
                out[qid] = EngineAnswer(None)
            elif a["type"] == "predicate":
                out[qid] = EngineAnswer({"yes": a["probability"]})
            elif a["type"] == "score":
                out[qid] = EngineAnswer({p["label"]: p["probability"] for p in a["probabilities"]})
            else:
                out[qid] = EngineAnswer({str(p["value"]): p["probability"] for p in a["probabilities"]})
        return out
