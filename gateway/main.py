"""게이트웨이 앱.

실행: uv run uvicorn gateway.main:app --host 127.0.0.1 --port 8000
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

from gateway.api.routes import openai_error, router
from gateway.config import Settings, load_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="AI 보안·비용 게이트웨이", version="0.0.1")
    app.state.settings = settings or load_settings()

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
