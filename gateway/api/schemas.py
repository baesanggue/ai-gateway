"""OpenAI 호환 Chat Completions 스키마.

필드는 OpenAI 공식 API 문서(developers.openai.com, POST /v1/chat/completions)를 기준으로 한다.
기존 서비스가 base URL만 바꿔도 동작해야 하므로(설계 결정 4번), 우리가 쓰지 않는 요청 필드도
거부하지 않고 받아 둔다(extra="allow").
"""
import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ContentPart(BaseModel):
    """content를 배열로 보낼 때의 한 조각. 지금은 text만 처리한다."""

    model_config = ConfigDict(extra="allow")
    type: str
    text: str | None = None


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: Literal["developer", "system", "user", "assistant", "tool", "function"]
    content: str | list[ContentPart] | None = None

    def text(self) -> str:
        """content를 문자열 하나로. 텍스트가 아닌 조각(이미지 등)이 있으면 ValueError."""
        if self.content is None:
            return ""
        if isinstance(self.content, str):
            return self.content
        texts = []
        for part in self.content:
            if part.type != "text" or part.text is None:
                raise ValueError(f"텍스트가 아닌 content 조각은 아직 지원하지 않습니다: {part.type}")
            texts.append(part.text)
        return "\n".join(texts)


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool = False
    temperature: float | None = None
    max_completion_tokens: int | None = None
    max_tokens: int | None = None  # OpenAI 문서상 폐기 예정, 호환을 위해 받는다
    n: int = 1


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str | None
    refusal: str | None = None


class Choice(BaseModel):
    index: int = 0
    message: AssistantMessage
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter", "function_call"] = "stop"
    logprobs: Any = None


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:24]}")
    object: Literal["chat.completion"] = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[Choice]
    usage: Usage = Usage()


class ErrorDetail(BaseModel):
    message: str
    type: str = "invalid_request_error"
    param: str | None = None
    code: str | None = None


class ErrorResponse(BaseModel):
    """OpenAI 형식 오류 본문. SDK가 이 형식을 읽어 예외 메시지를 만든다."""

    error: ErrorDetail
