"""OpenAI 호환 엔드포인트 (#7 형식, #12에서 요청 처리 흐름에 연결).

- 응답 본문은 OpenAI 형식 그대로 둔다(설계 결정 4번). 판단 과정은 본문에 넣지 않고,
  헤더 x-gateway-request-id로 요청 로그의 그 줄을 찾게 한다 (데모·관리 화면은 로그를 읽는다)
- 차단: HTTP 200 + 안내문을 답변으로 + finish_reason "content_filter". 기존 챗봇은 안내문을 그대로 보여 주고,
  프로그램은 차단됐다는 것을 알 수 있다
- LLM 오류: OpenAI 형식 502. 메시지에 상위 오류 내용을 넣지 않는다 (로그에는 오류 code가 남는다)
"""
from typing import get_args

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from gateway.api.schemas import (
    AssistantMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    ErrorDetail,
    ErrorResponse,
    Usage,
)

router = APIRouter()
REQUEST_ID_HEADER = "x-gateway-request-id"
FINISH_REASONS = set(get_args(Choice.model_fields["finish_reason"].annotation))


def openai_error(status: int, message: str, param: str | None = None, code: str | None = None,
                 type_: str = "invalid_request_error") -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(message=message, type=type_, param=param, code=code))
    return JSONResponse(status_code=status, content=body.model_dump())


@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
def chat_completions(req: ChatCompletionRequest, request: Request):
    if req.stream:
        return openai_error(400, "stream=true는 아직 지원하지 않습니다. stream=false로 보내 주세요.",
                            param="stream", code="unsupported_parameter")
    try:
        messages = [{"role": m.role, "content": m.text()} for m in req.messages]  # 텍스트가 아닌 조각은 여기서 거른다
    except ValueError as e:
        return openai_error(400, str(e), param="messages", code="unsupported_content")

    reply = request.app.state.gateway.handle(messages, req.max_completion_tokens or req.max_tokens)
    headers = {REQUEST_ID_HEADER: reply.request_id}
    if reply.error:
        resp = openai_error(502, "LLM 응답을 받지 못했습니다. 잠시 후 다시 시도해 주세요.",
                            code="upstream_error", type_="api_error")
        resp.headers.update(headers)
        return resp

    usage = reply.usage
    body = ChatCompletionResponse(
        id=f"chatcmpl-{reply.request_id}",
        model=reply.model or req.model,
        choices=[Choice(message=AssistantMessage(content=reply.text, refusal=reply.refusal),
                        finish_reason=reply.finish_reason if reply.finish_reason in FINISH_REASONS else "stop")],
        usage=Usage(prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
                    total_tokens=usage.prompt_tokens + usage.completion_tokens) if usage else Usage(),
    )
    return JSONResponse(body.model_dump(), headers=headers)
