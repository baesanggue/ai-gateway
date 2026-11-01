"""OpenAI 호환 엔드포인트 (#7 형식, #12에서 요청 처리 흐름에 연결).

- 인증(D20): GATEWAY_API_KEYS가 있으면 Bearer 키 필수, 없으면 같은 PC에서 온 요청만 받는다 (authorize)
- 응답 본문은 OpenAI 형식 그대로 둔다(설계 결정 4번). 판단 과정은 본문에 넣지 않고,
  헤더 x-gateway-request-id로 요청 로그의 그 줄을 찾게 한다 (데모·관리 화면은 로그를 읽는다)
- 차단: HTTP 200 + 안내문을 답변으로 + finish_reason "content_filter". 기존 챗봇은 안내문을 그대로 보여 주고,
  프로그램은 차단됐다는 것을 알 수 있다
- LLM 오류: OpenAI 형식 502. 메시지에 상위 오류 내용을 넣지 않는다 (로그에는 오류 code가 남는다)
"""
import hmac
import ipaddress
from typing import get_args
from urllib.parse import urlsplit

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


def authorize(request: Request) -> JSONResponse | None:
    """게이트웨이 키 확인 (D20). 통과하면 None, 막으면 OpenAI 형식 401.

    게이트웨이는 회사의 OpenAI 키로 LLM을 부른다. 아무나 닿을 수 있으면 그 키를 대신 써 주는 열린 프록시가 된다.
    - .env에 GATEWAY_API_KEYS가 있으면 모든 요청에 Authorization: Bearer <키>를 요구한다 (상수 시간 비교)
    - 없으면 같은 PC(루프백)에서 온 요청만 받는다. 시연·개발은 base URL만 바꾸면 되고, 외부에 열려면 키가 필요하다
    클라이언트가 보낸 키는 상위(OpenAI)로 넘기지 않고 로그에도 남기지 않는다.
    """
    keys = request.app.state.settings.secrets.gateway_api_keys
    valid = [k.strip() for k in (keys.get_secret_value() if keys else "").split(",") if k.strip()]
    if not valid:
        if not _is_local(request.client.host if request.client else ""):
            return openai_error(401, "게이트웨이 키가 설정되지 않아 같은 PC에서 온 요청만 받습니다. "
                                     "외부에서 쓰려면 .env에 GATEWAY_API_KEYS를 설정하세요.", code="invalid_api_key")
        # 같은 PC의 브라우저가 악성 웹페이지 때문에 보내는 요청은 막는다 (#12 보안 검토)
        # - DNS 리바인딩: 접속은 127.0.0.1이지만 Host 헤더에 공격자 도메인이 남는다
        # - 교차 사이트 요청: 브라우저는 Origin 헤더를 붙인다. SDK 같은 프로그램은 붙이지 않는다
        origin = request.headers.get("origin")
        if _host_name(request.headers.get("host", "")) not in LOCAL_HOST_NAMES or (
                origin is not None and urlsplit(origin).hostname not in LOCAL_ORIGIN_NAMES):
            return openai_error(403, "같은 PC의 프로그램에서 온 요청만 받습니다. 웹페이지에서 보낸 요청은 처리하지 않습니다.",
                                code="forbidden_origin")
        return None
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    matched = False
    for key in valid:            # 하나 맞아도 끝까지 비교한다 (몇 번째 키와 맞았는지 시간으로 드러나지 않게)
        matched |= hmac.compare_digest(token.encode(), key.encode())
    return None if matched else openai_error(401, "게이트웨이 키가 올바르지 않습니다.", code="invalid_api_key")


LOCAL_HOST_NAMES = {"127.0.0.1", "localhost", "[::1]", "testserver"}   # Host 헤더(포트 제외). testserver는 TestClient
LOCAL_ORIGIN_NAMES = {"127.0.0.1", "localhost", "::1"}


def _host_name(host: str) -> str:
    """Host 헤더에서 포트를 뗀다: "127.0.0.1:8000" → "127.0.0.1", "[::1]:8000" → "[::1]"."""
    host = host.strip().lower()
    return host.split("]")[0] + "]" if host.startswith("[") else host.split(":")[0]


def _is_local(host: str) -> bool:
    if host == "testclient":     # Starlette TestClient가 쓰는 이름. 실제 소켓 주소로는 나오지 않는다
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or (ip.version == 6 and ip.ipv4_mapped is not None and ip.ipv4_mapped.is_loopback)


@router.post("/v1/chat/completions", response_model=ChatCompletionResponse)
def chat_completions(req: ChatCompletionRequest, request: Request):
    if (denied := authorize(request)) is not None:
        return denied
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
