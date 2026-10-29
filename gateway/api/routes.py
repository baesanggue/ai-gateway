"""OpenAI 호환 엔드포인트.

#7 단계에서는 요청 형식만 받아 고정 응답을 돌려준다.
판단(#8) · LLM(#9) · 라우팅(#10) · 로그(#11)는 #12에서 이 자리에 연결한다.
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from gateway.api.schemas import (
    AssistantMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    ErrorDetail,
    ErrorResponse,
)

router = APIRouter()

FIXED_REPLY = "게이트웨이가 요청을 받았습니다. 판단·라우팅·답변 생성은 아직 연결 전입니다."


def openai_error(status: int, message: str, param: str | None = None, code: str | None = None) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(message=message, param=param, code=code))
    return JSONResponse(status_code=status, content=body.model_dump())


@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
def chat_completions(req: ChatCompletionRequest):
    if req.stream:
        return openai_error(400, "stream=true는 아직 지원하지 않습니다. stream=false로 보내 주세요.",
                            param="stream", code="unsupported_parameter")
    try:
        for m in req.messages:
            m.text()  # 텍스트가 아닌 조각이 있으면 여기서 거른다
    except ValueError as e:
        return openai_error(400, str(e), param="messages", code="unsupported_content")
    return ChatCompletionResponse(
        model=req.model,
        choices=[Choice(message=AssistantMessage(content=FIXED_REPLY))],
    )
