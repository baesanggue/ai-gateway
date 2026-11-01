"""게이트웨이 앱.

실행: uv run uvicorn gateway.main:app --host 127.0.0.1 --port 8000
전제: .env에 OPENAI_API_KEY, Ollaya가 config.yaml의 decision.base_url에서 실행 중
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

from gateway.api.routes import openai_error, router
from gateway.config import Settings, load_settings
from gateway.masking.masker import Masker
from gateway.pipeline import Gateway
from gateway.storage.log import RequestLog


def create_app(settings: Settings | None = None, *, transport=None, masker: Masker | None = None,
               log: RequestLog | None = None) -> FastAPI:
    """transport·masker·log는 테스트에서 가짜 상위 서버, 가짜 가명화, 임시 DB를 넣을 때 쓴다."""
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 판단 엔진·LLM 클라이언트(httpx 연결)와 로그는 시작할 때 한 번 만들고 끝날 때 닫는다
        app.state.gateway = Gateway.from_settings(settings, transport=transport, masker=masker, log=log)
        yield
        app.state.gateway.close()

    app = FastAPI(title="AI 보안·비용 게이트웨이", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        # FastAPI 기본(422) 대신 OpenAI처럼 400 + {"error": {...}} 형식으로 돌려준다
        first = exc.errors()[0] if exc.errors() else {}
        param = ".".join(str(x) for x in first.get("loc", [])[1:]) or None
        return openai_error(400, f"요청 형식 오류: {first.get('msg', '알 수 없음')}", param=param, code="invalid_request")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "security_level": app.state.settings.config.security_level}

    app.include_router(router)
    return app


app = create_app()
