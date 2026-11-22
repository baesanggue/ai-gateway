"""OpenAI 호환 엔드포인트 (#7 형식, #12에서 요청 처리 흐름에 연결).

- 인증(D20): GATEWAY_API_KEYS가 있으면 Bearer 키 필수, 없으면 같은 PC에서 온 요청만 받는다 (authorize)
- 응답 본문은 OpenAI 형식 그대로 둔다(설계 결정 4번). 판단 과정은 본문에 넣지 않고,
  헤더 x-gateway-request-id로 요청 로그의 그 줄을 찾게 한다 (데모·관리 화면은 로그를 읽는다)
- 차단: HTTP 200 + 안내문을 답변으로 + finish_reason "content_filter". 기존 챗봇은 안내문을 그대로 보여 주고,
  프로그램은 차단됐다는 것을 알 수 있다
- LLM 오류: OpenAI 형식 502. 메시지에 상위 오류 내용을 넣지 않는다 (로그에는 오류 code가 남는다)
- stream=true (#84, D32): 처리·복원을 끝까지 한 뒤 완성된 답을 OpenAI 스트리밍 형식(SSE)으로 보낸다.
  LLM 답을 조각째 넘기면 [고객1]이 조각 경계에서 잘려 복원하기 어렵고, 복원 전 조각이 나가면 가명이 그대로 보인다.
  첫 글자까지 시간은 줄지 않지만 SDK의 stream 호출이 그대로 동작한다. 오류는 보내기 전에 정해지므로 보통 오류 응답으로 온다
"""
import base64
import binascii
import hmac
import ipaddress
import json
from collections.abc import Iterator
from dataclasses import asdict
from typing import get_args
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from gateway.files.extract import FileError
from gateway.files.scan import scan, summary
from gateway.api.schemas import (
    AssistantMessage,
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    ChunkChoice,
    ChunkDelta,
    ErrorDetail,
    ErrorResponse,
    FileScanRequest,
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
        problem = local_problem(request)
        if problem == "remote":
            return openai_error(401, "게이트웨이 키가 설정되지 않아 같은 PC에서 온 요청만 받습니다. "
                                     "외부에서 쓰려면 .env에 GATEWAY_API_KEYS를 설정하세요.", code="invalid_api_key")
        if problem == "browser":
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


def local_problem(request: Request) -> str | None:
    """같은 PC의 프로그램이 보낸 요청이면 None. 아니면 "remote"(다른 PC) | "browser"(웹페이지가 보냄).

    같은 PC의 브라우저가 악성 웹페이지 때문에 보내는 요청도 막는다 (#12 보안 검토)
    - DNS 리바인딩: 접속은 127.0.0.1이지만 Host 헤더에 공격자 도메인이 남는다
    - 교차 사이트 요청: 브라우저는 Origin 헤더를 붙인다. SDK 같은 프로그램은 붙이지 않는다
    """
    if not _is_local(request.client.host if request.client else ""):
        return "remote"
    origin = request.headers.get("origin")
    if _host_name(request.headers.get("host", "")) not in LOCAL_HOST_NAMES or (
            origin is not None and urlsplit(origin).hostname not in LOCAL_ORIGIN_NAMES):
        return "browser"
    return None


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
    if req.stream:
        include_usage = bool(req.stream_options and req.stream_options.include_usage)
        return StreamingResponse(stream_events(body, include_usage), media_type="text/event-stream",
                                 headers=headers | {"cache-control": "no-cache"})
    return JSONResponse(body.model_dump(), headers=headers)


def stream_events(body: ChatCompletionResponse, include_usage: bool) -> Iterator[str]:
    """완성된 답을 OpenAI 스트리밍 형식의 SSE 조각으로 (#84). 복원까지 끝난 답이라 가명 토큰이 조각나지 않는다.

    순서: 역할 → 내용 → (LLM이 거부했으면) 거부 문구 → 끝난 이유 → (include_usage면) 토큰 수 → [DONE].
    조각 사이에 일부러 늦추지 않는다 (11/22: 타자 효과를 흉내 내면 전체 시간만 늘어난다).
    """
    message, finish = body.choices[0].message, body.choices[0].finish_reason

    def event(choices: list[ChunkChoice], usage: Usage | None = None) -> str:
        chunk = ChatCompletionChunk(id=body.id, created=body.created, model=body.model, choices=choices).model_dump()
        for out, choice in zip(chunk["choices"], choices):
            out["delta"] = choice.delta.model_dump(exclude_unset=True)     # 보낸 칸만 (끝 조각은 빈 delta {})
        if include_usage:            # SDK 설명: 마지막 조각만 usage가 있고 나머지는 null
            chunk["usage"] = usage.model_dump() if usage else None
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    yield event([ChunkChoice(delta=ChunkDelta(role="assistant", content=""))])
    if message.content:
        yield event([ChunkChoice(delta=ChunkDelta(content=message.content))])
    if message.refusal:
        yield event([ChunkChoice(delta=ChunkDelta(refusal=message.refusal))])
    yield event([ChunkChoice(delta=ChunkDelta(), finish_reason=finish)])
    if include_usage:
        yield event([], body.usage)
    yield "data: [DONE]\n\n"


@router.post("/v1/files/scan")
def scan_file(req: FileScanRequest, request: Request):
    """파일 속 개인정보 검사 (#50). 게이트웨이 키 규칙은 채팅과 같다. 결과에 값은 넣지 않는다.
    가명화 부품이 가명화를 못 하는 부품이면 검사하지 않는다 (규칙이 없으면 "없음"이라고 잘못 안내하게 된다)."""
    if (denied := authorize(request)) is not None:
        return denied
    masker = request.app.state.gateway.masker
    if not getattr(masker, "available", False):
        return openai_error(503, "가명화 규칙이 준비되지 않아 파일을 검사할 수 없습니다.", code="masking_unavailable",
                            type_="api_error")
    try:
        data = base64.b64decode(req.content_base64, validate=True)
    except (binascii.Error, ValueError):
        return openai_error(400, "content_base64가 올바른 base64가 아닙니다.", param="content_base64", code="invalid_file")
    try:
        report = scan(req.filename, data, masker)
    except FileError as e:
        return openai_error(400, str(e), param="filename" if e.code == "unsupported" else None, code=e.code)
    return {"has_pii": report.has_pii, "summary": summary(report), **asdict(report)}
