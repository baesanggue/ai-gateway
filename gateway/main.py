"""게이트웨이 앱.

실행: uv run uvicorn gateway.main:app --host 127.0.0.1 --port 8000
전제: .env에 OPENAI_API_KEY, Ollaya가 config.yaml의 decision.base_url에서 실행 중
"""
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager

import yaml
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from gateway.api.routes import local_problem, openai_error, router
from gateway.config import Settings, load_settings
from gateway.masking.masker import Masker
from gateway.pipeline import Gateway
from gateway.storage.log import RequestLog

# 설정을 다시 읽은 뒤 이전 게이트웨이(판단·LLM 연결)를 닫기까지 기다리는 시간. 그 사이 처리 중이던 요청은
# 이전 설정으로 끝난다. LLM 시간 제한(60초)과 판단 시간 제한(10초 + 10초)보다 길게 잡는다
RETIRE_SECONDS = 180
# 설정을 다시 읽다 실패할 수 있는 이유: 설정 형식(ValueError·pydantic), 사전·지시문·모델 파일 없음(RuntimeError), 파일 읽기
RELOAD_ERRORS = (ValueError, RuntimeError, OSError, yaml.YAMLError)


def create_app(settings: Settings | None = None, *, transport=None, masker: Masker | None = None,
               log: RequestLog | None = None, reload_settings: Callable[[], Settings] | None = None) -> FastAPI:
    """transport·masker·log는 테스트에서 가짜 상위 서버, 가짜 가명화, 임시 DB를 넣을 때 쓴다.
    reload_settings: /admin/reload가 설정을 다시 읽는 함수 (기본은 load_settings, 테스트에서 바꾼다)."""
    settings = settings or load_settings()
    reload_settings = reload_settings or load_settings
    reload_lock = threading.Lock()

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
        masker = app.state.gateway.masker
        return {"status": "ok", "security_level": app.state.settings.config.security_level,
                "preset": app.state.settings.config.preset,
                # "example"이면 시연용 가상 사전으로 동작 중이다. 실제 고객명은 가려지지 않는다 (D21)
                "dictionary": getattr(masker, "dictionary_source", None),
                # 이름 인식 모델 파일 (D23). 없으면 게이트웨이가 시작하지 않으므로, 실제 운영에서는 늘 값이 있다
                "ner": getattr(masker, "ner_source", None)}

    @app.post("/admin/reload")
    def reload(request: Request):
        """설정을 다시 읽어 다음 요청부터 쓴다 (D26, 화면의 설정 메뉴가 부른다). 다시 시작하지 않아도 된다.

        - 같은 PC의 프로그램만 부를 수 있다. 게이트웨이 키를 설정해 외부에 열어 두어도 이것은 열지 않는다
        - 시작할 때와 똑같이 만든다: 사전·지시문·모델 파일이 없거나 설정이 틀리면 실패하고, 지금 설정을 그대로 쓴다
        - 처리 중이던 요청은 이전 설정으로 끝나고, 이전 연결은 잠시 뒤에 닫는다
        """
        if local_problem(request) is not None:
            return JSONResponse(status_code=403, content={"status": "error",
                                                          "message": "설정 다시 읽기는 같은 PC의 프로그램에서만 할 수 있습니다."})
        with reload_lock:                  # 두 번 동시에 눌러도 차례로 만든다
            try:
                new_settings = reload_settings()
                new_gateway = Gateway.from_settings(new_settings, transport=transport, masker=masker, log=log)
            except RELOAD_ERRORS as e:
                return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
            old = app.state.gateway
            app.state.settings, app.state.gateway = new_settings, new_gateway
        timer = threading.Timer(RETIRE_SECONDS, old.close)
        timer.daemon = True
        timer.start()
        return {"status": "ok", "security_level": new_settings.config.security_level, "preset": new_settings.config.preset}

    app.include_router(router)
    return app


app = create_app()
