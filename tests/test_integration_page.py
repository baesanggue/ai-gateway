"""연동 안내 화면 (#133). 완료 기준: 화면의 예시 코드를 그대로 붙여 넣으면 게이트웨이로 요청이 간다.

- 게이트웨이를 실제 포트에 켜고(상위 서버는 가짜), 화면이 보여 주는 Python 예시를 한 글자도 바꾸지 않고 다른 프로세스에서
  실행한다. 붙여 넣는 사람의 프로그램처럼 이 저장소 밖 폴더에서 돌린다
- 화면과 docs/integration.md가 같은 예시를 쓰는지, 오류 표가 게이트웨이의 오류 code를 빠짐없이 담는지 본다
- 게이트웨이 키는 있는지만 보이고 값은 화면에 나오지 않는다
JavaScript 예시는 openai npm 패키지가 있어야 해서 자동 테스트에 넣지 않았다(11/21 한 번 실행해 확인, PR에 기록).
"""
import inspect
import os
import re
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import uvicorn
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from admin.integration_page import (  # noqa: E402
    ERRORS, js_example, js_stream_example, python_example, python_stream_example)
from gateway.api import routes  # noqa: E402
from gateway.config import GatewayConfig  # noqa: E402
from gateway.main import create_app  # noqa: E402
from gateway.storage.log import RequestLog  # noqa: E402
from tests.fakes import FakeMasker, FakeUpstream, test_settings  # noqa: E402
from tests.test_first_screen import screen_env  # noqa: E402,F401

DEFAULT_BASE = f"http://{GatewayConfig().host}:{GatewayConfig().port}/v1"     # 설치 직후 주소 (문서에 적는 값)
SCREEN = f"""
import sys
sys.path.insert(0, r"{ROOT}")
from admin import integration_page
integration_page.page()
"""


@contextmanager
def running_gateway(tmp_path, upstream):
    """게이트웨이를 127.0.0.1의 빈 포트에 켠다. 판단·LLM은 가짜 상위 서버, 가명화는 가짜 부품(모델 파일 불필요)."""
    app = create_app(test_settings(tmp_path), transport=httpx.MockTransport(upstream), masker=FakeMasker(),
                     log=RequestLog(tmp_path / "gateway.db"))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline, "게이트웨이가 켜지지 않았다"
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{sock.getsockname()[1]}/v1"
    finally:
        server.should_exit = True
        thread.join(15)
        sock.close()


def run_pasted(code: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """붙여 넣은 코드를 이 저장소 밖 폴더의 다른 파이썬 프로세스에서 돌린다 (붙여 넣는 사람의 프로그램)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPENAI_")}
    env |= {"PYTHONIOENCODING": "utf-8", "NO_PROXY": "127.0.0.1,localhost"}
    work = tmp_path / "my-service"
    work.mkdir(exist_ok=True)
    return subprocess.run([sys.executable, "-c", code], cwd=work, env=env, capture_output=True,
                          text=True, encoding="utf-8", timeout=90)


def test_python_example_runs_unchanged_and_goes_through_the_gateway(tmp_path):
    upstream = FakeUpstream(reply="[고객1] 고객님, 배송이 늦어져 죄송합니다.")
    with running_gateway(tmp_path, upstream) as base:
        run = run_pasted(python_example(base), tmp_path)          # 화면에 보이는 그대로
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "김민수 고객님, 배송이 늦어져 죄송합니다."     # 이 PC에서 되돌린 답변
    sent = upstream.raw_bytes().decode("utf-8")
    assert "[고객1]" in sent and "[전화번호1]" in sent                     # 게이트웨이를 거쳐 가려진 채로 나갔다
    assert "김민수" not in sent and "010-1234-5678" not in sent
    assert len(RequestLog(tmp_path / "gateway.db").recent(10)) == 1         # 요청 기록에 한 줄


def test_python_stream_example_runs_unchanged(tmp_path):
    """문서의 stream=True 예시 (#84): 실제 소켓으로 SSE 조각을 받아 이어 붙인다."""
    upstream = FakeUpstream(reply="[고객1] 고객님, 배송이 늦어져 죄송합니다.")
    with running_gateway(tmp_path, upstream) as base:
        run = run_pasted(python_stream_example(base), tmp_path)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "김민수 고객님, 배송이 늦어져 죄송합니다."
    assert "김민수" not in upstream.raw_bytes().decode("utf-8")


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_docs_show_the_same_examples_as_the_screen():
    docs = _read("docs", "integration.md")
    assert f"```python\n{python_example(DEFAULT_BASE)}```" in docs
    assert f"```javascript\n{js_example(DEFAULT_BASE)}```" in docs
    assert f"```python\n{python_stream_example(DEFAULT_BASE)}```" in docs                # 스트리밍 (#84)
    assert f"```javascript\n{js_stream_example(DEFAULT_BASE)}```" in docs
    for _, code, _, _ in ERRORS:
        assert f"`{code}`" in docs
    assert f"```python\n{python_example(DEFAULT_BASE)}```" in _read("README.md")     # README 3장도 같은 예시


def test_error_table_has_every_code_the_chat_endpoint_returns():
    source = inspect.getsource(routes.chat_completions) + inspect.getsource(routes.authorize)
    source += (ROOT / "gateway" / "main.py").read_text(encoding="utf-8")      # 요청 형식 오류(invalid_request)
    returned = set(re.findall(r'code="([a-z_]+)"', source))
    assert returned == {code for _, code, _, _ in ERRORS}


def _all_text(at) -> str:
    return "\n".join([m.value for m in at.markdown] + [c.value for c in at.caption] + [c.value for c in at.code])


def test_screen_shows_the_examples_for_this_gateway_address(screen_env, monkeypatch):
    monkeypatch.setenv("GATEWAY_API_KEYS", "")
    at = AppTest.from_string(SCREEN, default_timeout=60).run()
    assert not at.exception
    from admin.ui import gateway_url, settings
    base = f"{gateway_url(settings())}/v1"
    assert [c.value.rstrip("\n") for c in at.code] == [python_example(base).rstrip("\n"), js_example(base).rstrip("\n")]
    assert "정하지 않음" in _all_text(at)


def test_screen_shows_only_that_a_gateway_key_is_set_not_the_key(screen_env, monkeypatch):
    monkeypatch.setenv("GATEWAY_API_KEYS", "gw-test-only-key-1,gw-test-only-key-2")
    at = AppTest.from_string(SCREEN, default_timeout=60).run()
    assert not at.exception
    text = _all_text(at)
    assert "<b>정함</b>" in text and "gw-test-only-key" not in text
