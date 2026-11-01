"""통합 테스트용 가짜: 상위 서버(Ollaya·OpenAI Decisions·OpenAI Chat)와 가짜 가명화 부품.

실제 서버 대신 httpx.MockTransport에 FakeUpstream을 넣는다. 어떤 요청이 어디로 갔는지 기록해서
"원본이 밖으로 나가지 않았다", "외부 호출이 0건이다"를 확인할 수 있다.
"""
import json
import re

import httpx

from gateway.masking.masker import MaskResult
from gateway.masking.patterns import PATTERNS

TEST_KEY = "sk-test-only-not-a-real-key"


def noul(p):
    return {"type": "noul", "noul": p}


def predicate(name, p):
    return {"type": "predicate", "name": name, "probability": p}


def refusal(name):
    return {"type": "refusal", "name": name}


def difficulty(p_complex):
    return {"type": "score", "name": "difficulty", "score": p_complex, "confidence": 0.5,
            "probabilities": [{"value": 0, "label": "simple", "probability": 1 - p_complex},
                              {"value": 1, "label": "complex", "probability": p_complex}]}


def intent(**probs):
    return {"type": "choice", "name": "intent", "choice": max(probs, key=probs.get), "confidence": 0.5,
            "probabilities": [{"value": k, "probability": v} for k, v in probs.items()]}


def completion(content, prompt_tokens=120, completion_tokens=80, finish_reason="stop"):
    return {"id": "chatcmpl-upstream", "object": "chat.completion", "model": "upstream-name",
            "choices": [{"index": 0, "finish_reason": finish_reason, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens,
                      "completion_tokens_details": {"reasoning_tokens": 0}}}


class FakeUpstream:
    """기본값: 개인정보·기밀 없음(로컬), 위험·원본 필요 없음·단순·이용 문의(클라우드), LLM은 고정 답변.
    바꾸고 싶은 판단만 local={...}, cloud={...}로 준다. *_error는 그 상위 서버가 실패하게 한다."""

    def __init__(self, local=None, cloud=None, history=None, reply="네, 안내해 드리겠습니다.", local_error=False,
                 cloud_error=False, chat_status=200):
        self.local = {"has_pii": noul(0.01), "is_confidential": noul(0.01)} | (local or {})
        self.cloud = {"needs_raw_value": predicate("needs_raw_value", 0.0), "is_risky": predicate("is_risky", 0.0),
                      "difficulty": difficulty(0.01),
                      "intent": intent(faq=0.1, service=0.7, task=0.1, off_topic=0.1)} | (cloud or {})
        # 이전 대화·도구 결과의 위험 판단 (위험 질문 하나만 묻는 호출)
        self.history = {"is_risky": predicate("is_risky", 0.0)} | (history or {})
        self.reply = reply
        self.local_error, self.cloud_error, self.chat_status = local_error, cloud_error, chat_status
        self.requests: list[httpx.Request] = []

    def bodies(self, path):
        return [json.loads(r.content) for r in self.requests if r.url.path == path]

    @property
    def external_calls(self):
        """밖(OpenAI)으로 나간 요청 수. 로컬 Ollaya 호출은 세지 않는다."""
        return sum(r.url.host == "api.openai.com" for r in self.requests)

    def raw_bytes(self):
        return b"".join(r.content for r in self.requests if r.url.host == "api.openai.com")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content)
        if request.url.path == "/v1/systemone":
            if self.local_error:
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(200, json={"model": body["model"], "usage": {"input_tokens": 40},
                                             "answers": {q: self.local[q] for q in body["questions"]}})
        if request.url.path == "/v1/decisions":
            if self.cloud_error:
                raise httpx.ReadTimeout("timed out", request=request)
            names = [q["name"] for q in body["questions"]]
            answers = self.history if names == ["is_risky"] else self.cloud
            return httpx.Response(200, json={"model": "gpt-6-luna", "usage": {"input_tokens": 150},
                                             "answers": [answers[n] for n in names]})
        if request.url.path == "/v1/chat/completions":
            if self.chat_status != 200:
                return httpx.Response(self.chat_status, json={"error": {"message": "upstream", "code": "server_error"}})
            return httpx.Response(200, json=completion(self.reply))
        return httpx.Response(404)


class FakeMasker:
    """테스트용 가명화: 등록된 이름 + 정규식 값을 [종류N] 토큰으로 바꾸고 되돌린다. 진짜 가명화는 M2에서 만든다."""

    available = True

    def __init__(self, names=("김민수",)):
        self.names = names

    def mask(self, messages):
        mapping, counts, by_value = {}, {}, {}

        def token(kind, value):
            if value not in by_value:
                counts[kind] = counts.get(kind, 0) + 1
                by_value[value] = f"[{kind}{counts[kind]}]"
                mapping[by_value[value]] = value
            return by_value[value]

        out = []
        for m in messages:
            text = m["content"]
            for name in self.names:
                if name in text:
                    text = text.replace(name, token("고객", name))
            for kind, pattern in PATTERNS:
                text = pattern.sub(lambda mt, k=kind: token(k, mt.group()), text)
            out.append({"role": m["role"], "content": text})
        return MaskResult(out, mapping, counts)

    def restore(self, text, mapping):
        return re.sub(r"\[[^\[\]]+\d+\]", lambda m: mapping.get(m.group(), m.group()), text)
