"""파일 글자 뽑기를 별도 프로세스에서 한다 (#52). 파일 검사(scan.py)가 부른다.

파일 검사와 채팅이 같은 프로세스면, 악성 파일 하나가 글자 뽑기 라이브러리(pypdf 등) 안에서 시간·메모리를 많이 써
게이트웨이 전체를 느리게 하거나 멈출 수 있다(개인정보 유출이 아니라 가용성 문제). 그래서 파일마다 작업 프로세스(worker.py)를 띄운다.
- 메모리: 작업 프로세스가 시작하자마자 자기에게 WORKER_MEMORY_MB 상한을 건다. 넘으면 too_long으로 안내한다
- 시간: WORKER_SECONDS가 넘으면 작업 프로세스를 강제로 끄고 too_long으로 안내한다
  (PDF 자체 상한 30초보다 길게 잡아, 보통은 그 상한이 먼저 이유를 알려 준다)
- 동시에 MAX_WORKERS개까지만 띄운다. 최악이어도 메모리는 WORKER_MEMORY_MB × MAX_WORKERS로 묶인다. 넘치면 차례를 기다린다
- 형식·크기는 파일을 열지 않고 여기서 먼저 본다(지원하지 않는 형식에 프로세스를 띄우지 않는다)
- 창 없이 띄운다(Windows CREATE_NO_WINDOW): start.ps1이 창 없이 켠 게이트웨이에서 검은 창이 번쩍이지 않게
값은 11/23 운영자가 골랐다(1GB · 60초 · 동시 2개). 추가 지연은 docs/file-scan.md
"""
import json
import subprocess
import sys
import threading

from gateway.config import ROOT
from gateway.files.extract import FileError, Segment, check_file

WORKER_MEMORY_MB = 1024
WORKER_SECONDS = 60
MAX_WORKERS = 2
WORKER_MODULE = "gateway.files.worker"      # 시험에서 멈추거나 메모리를 먹는 작업 프로세스로 바꿔 끼운다
_slots = threading.BoundedSemaphore(MAX_WORKERS)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def extract_isolated(filename: str, data: bytes) -> list[Segment]:
    """extract.extract와 같은 결과를 별도 프로세스에서 얻는다. 실패는 같은 FileError로 알린다."""
    ext = check_file(filename, data)
    seconds = WORKER_SECONDS
    with _slots:
        try:
            # 파일 이름은 확장자만 넘긴다(글자 뽑기는 확장자만 쓴다). 셸을 거치지 않는다
            run = subprocess.run([sys.executable, "-m", WORKER_MODULE, f"file{ext}", str(WORKER_MEMORY_MB)],
                                 input=data, capture_output=True, timeout=seconds, cwd=ROOT, creationflags=_NO_WINDOW)
        except subprocess.TimeoutExpired:          # subprocess.run이 작업 프로세스를 끄고 나서 알린다
            raise FileError("too_long", f"파일을 읽는 데 {seconds}초가 넘게 걸려 멈췄습니다. "
                                        "더 작은 파일로 나눠 검사해 주세요.") from None
    # 오류 출력(stderr)은 쓰지 않는다: 라이브러리 오류에 파일 내용 일부가 섞일 수 있다
    try:
        reply = json.loads(run.stdout.decode("utf-8"))
        if reply.get("ok"):
            return [Segment(where, text) for where, text in reply["segments"]]
        code, message = reply["code"], reply["message"]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError):
        raise FileError("unreadable", "파일을 읽다가 검사 프로세스가 멈췄습니다. 파일이 손상되었거나 지나치게 복잡할 수 있습니다.") from None
    raise FileError(code, message)
