"""파일 검사를 별도 프로세스에서 (#52). 완료 기준: 시간·메모리를 넘기는 파일에서도 게이트웨이 본체가 계속 응답한다.

- 같은 파일을 프로세스 안과 밖에서 뽑은 결과가 같다. 실패 이유(code)도 같게 돌아온다
- 멈추는 작업 프로세스는 시간 상한에서 꺼지고, 메모리를 먹는 작업 프로세스는 메모리 상한에서 멈춘다
  (tests/file_workers/: 실제 작업 프로세스 코드에 글자 뽑기 함수만 바꿔 넣은 것)
- 작업 프로세스가 멈춰 있는 동안에도 실제 포트의 게이트웨이가 채팅·상태 확인에 바로 답한다
"""
import base64
import io
import subprocess
import threading
import time
import zipfile

import httpx
import pytest

from gateway.files import isolate
from gateway.files.extract import FileError, extract
from gateway.files.worker import MEMORY_MESSAGE
from tests.fakes import FakeUpstream
from tests.test_files import make_docx, make_pdf, make_xlsx
from tests.test_integration_page import running_gateway

CSV = "이름,전화\n김민수,010-1234-5678\n".encode("utf-8")


@pytest.mark.parametrize("name, data", [
    ("고객기록.docx", make_docx()), ("고객명단.xlsx", make_xlsx()),
    ("안내문.pdf", make_pdf("Contact 010-9876-5432")), ("명단.csv", CSV),
], ids=["docx", "xlsx", "pdf", "csv"])
def test_isolated_extract_matches_in_process(name, data):
    assert isolate.extract_isolated(name, data) == extract(name, data)


def _zip_bomb() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"0" * 5_000_000)
    return buf.getvalue()


@pytest.mark.parametrize("name, data, code", [
    ("가짜.pdf", b"hello", "unreadable"), ("스캔.pdf", make_pdf(None), "no_text"), ("폭탄.docx", _zip_bomb(), "zip_bomb"),
], ids=["fake-pdf", "scanned-pdf", "zip-bomb"])
def test_file_errors_come_back_with_the_same_code(name, data, code):
    with pytest.raises(FileError) as e:
        isolate.extract_isolated(name, data)
    assert e.value.code == code


@pytest.mark.parametrize("name, data, code", [
    ("문서.hwp", b"anything", "unsupported"), ("큰.txt", b"x" * (10 * 1024 * 1024 + 1), "too_large"),
], ids=["unsupported", "too-large"])
def test_type_and_size_are_checked_without_starting_a_worker(monkeypatch, name, data, code):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("작업 프로세스를 띄웠다"))
    with pytest.raises(FileError) as e:
        isolate.extract_isolated(name, data)
    assert e.value.code == code


def test_hanging_worker_is_stopped_at_the_time_limit(monkeypatch):
    monkeypatch.setattr(isolate, "WORKER_MODULE", "tests.file_workers.hang")
    monkeypatch.setattr(isolate, "WORKER_SECONDS", 2)
    t0 = time.monotonic()
    with pytest.raises(FileError) as e:
        isolate.extract_isolated("메모.txt", CSV)
    assert e.value.code == "too_long" and "2초" in str(e.value)
    assert time.monotonic() - t0 < 10                       # 120초를 기다리지 않는다


def test_memory_hog_worker_is_stopped_at_the_memory_limit(monkeypatch):
    monkeypatch.setattr(isolate, "WORKER_MODULE", "tests.file_workers.greedy")
    monkeypatch.setattr(isolate, "WORKER_MEMORY_MB", 256)
    with pytest.raises(FileError) as e:
        isolate.extract_isolated("메모.txt", CSV)
    assert e.value.code == "too_long" and str(e.value) == MEMORY_MESSAGE


def test_crashing_worker_is_reported_as_unreadable(monkeypatch):
    monkeypatch.setattr(isolate, "WORKER_MODULE", "tests.file_workers.crash")
    with pytest.raises(FileError) as e:
        isolate.extract_isolated("메모.txt", CSV)
    assert e.value.code == "unreadable"


def test_gateway_keeps_answering_while_a_file_worker_hangs(tmp_path, monkeypatch):
    """완료 기준: 파일 하나가 작업 프로세스를 붙잡고 있어도 채팅·상태 확인은 바로 답한다 (실제 포트)."""
    monkeypatch.setattr(isolate, "WORKER_MODULE", "tests.file_workers.hang")
    monkeypatch.setattr(isolate, "WORKER_SECONDS", 6)
    with running_gateway(tmp_path, FakeUpstream(reply="네, 안내해 드리겠습니다.")) as base:
        root = base.removesuffix("/v1")
        result = {}

        def upload():
            result["r"] = httpx.post(f"{base}/files/scan", timeout=30,
                                     json={"filename": "메모.txt", "content_base64": base64.b64encode(CSV).decode()})

        worker = threading.Thread(target=upload)
        worker.start()
        time.sleep(1.0)                                     # 작업 프로세스가 떠서 멈춰 있는 동안
        timings = []
        for _ in range(3):
            t0 = time.monotonic()
            assert httpx.get(f"{root}/healthz", timeout=5).status_code == 200
            chat = httpx.post(f"{base}/chat/completions", timeout=5,
                              json={"model": "m", "messages": [{"role": "user", "content": "배송 지연 사과 메일 써줘"}]})
            assert chat.status_code == 200
            timings.append(time.monotonic() - t0)
        assert worker.is_alive()                            # 파일 검사는 아직 붙잡혀 있다
        worker.join(30)
    assert max(timings) < 2.0
    assert result["r"].status_code == 400 and result["r"].json()["error"]["code"] == "too_long"
