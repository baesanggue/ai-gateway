"""파일 글자 뽑기 작업 프로세스 (#52). 게이트웨이가 파일마다 띄운다(isolate.py): python -m gateway.files.worker <파일 이름> <메모리 MB>

악성 파일 하나가 글자 뽑기 라이브러리(pypdf 등) 안에서 시간·메모리를 많이 써도 게이트웨이 본체(채팅)는 멈추지 않게 한다.
- 시작하자마자 **자기 메모리에 상한을 건다**: Windows는 Job Object(프로세스 메모리 상한), 그 밖은 setrlimit(RLIMIT_AS).
  상한을 걸지 못하면 파일을 읽지 않는다(isolation_failed)
- 파일 내용은 stdin, 결과는 stdout에 JSON 한 줄(UTF-8)
  {"ok": true, "segments": [[위치, 글자], ...]} | {"ok": false, "code": ..., "message": ...}
- 글자만 돌려준다. 가명화는 게이트웨이가 한다(이름 인식 모델을 파일마다 다시 올리지 않게). 이 프로세스는 네트워크를 쓰지 않는다
- 시간 상한은 게이트웨이가 건다(넘으면 이 프로세스를 강제로 끈다)
"""
import json
import sys
from collections.abc import Callable

MEMORY_MESSAGE = "파일을 읽는 데 메모리를 너무 많이 써서 멈췄습니다. 더 작은 파일로 나눠 검사해 주세요."


def limit_this_process(memory_mb: int) -> None:
    """이 프로세스가 쓸 수 있는 메모리를 memory_mb로 묶는다. 넘으면 할당이 실패해 MemoryError가 난다."""
    limit = memory_mb * 1024 * 1024
    if sys.platform == "win32":
        _windows_job_limit(limit)
    else:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def _windows_job_limit(limit: int) -> None:
    """Job Object를 만들어 프로세스 메모리 상한(JOB_OBJECT_LIMIT_PROCESS_MEMORY)을 걸고 이 프로세스를 넣는다.
    Win32 API: CreateJobObjectW, SetInformationJobObject(JobObjectExtendedLimitInformation=9), AssignProcessToJobObject.
    Windows 8부터는 이미 다른 Job 안에 있어도(겹친 Job) 넣을 수 있다."""
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                      "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BasicLimit(ctypes.Structure):           # JOBOBJECT_BASIC_LIMIT_INFORMATION
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class ExtendedLimit(ctypes.Structure):        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        _fields_ = [("BasicLimitInformation", BasicLimit), ("IoInfo", IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW 실패")
    info = ExtendedLimit()
    info.BasicLimitInformation.LimitFlags = 0x00000100        # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    info.ProcessMemoryLimit = limit
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject 실패")
    if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject 실패")
    # job 핸들은 닫지 않는다: 프로세스가 끝날 때 운영체제가 닫는다


def _reply(obj: dict) -> None:
    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.flush()


def main(extract_fn: Callable | None = None) -> int:
    """extract_fn: 글자 뽑기 함수 (기본 gateway.files.extract.extract). 시험에서 멈추거나 메모리를 먹는 함수를 넣는다."""
    filename, memory_mb = sys.argv[1], int(sys.argv[2])
    try:
        limit_this_process(memory_mb)
    except Exception as e:                       # 상한 없이 읽지 않는다
        _reply({"ok": False, "code": "isolation_failed",
                "message": f"검사 프로세스에 메모리 상한을 걸지 못해 검사하지 않았습니다 ({type(e).__name__})."})
        return 3
    data = sys.stdin.buffer.read()
    from gateway.files.extract import FileError, extract
    try:
        segments = (extract_fn or extract)(filename, data)
    except FileError as e:
        _reply({"ok": False, "code": e.code, "message": str(e)})
        return 0
    except MemoryError:
        _reply({"ok": False, "code": "too_long", "message": MEMORY_MESSAGE})
        return 0
    except Exception as e:                       # 라이브러리 안의 예상하지 못한 오류. 내용(파일 글자)은 넣지 않는다
        _reply({"ok": False, "code": "unreadable", "message": f"파일을 읽지 못했습니다 ({type(e).__name__})."})
        return 0
    _reply({"ok": True, "segments": [[s.where, s.text] for s in segments]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
