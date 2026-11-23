"""파일 검사 별도 프로세스의 추가 지연 (#52): 같은 파일을 프로세스 안(extract)과 밖(extract_isolated)에서 뽑아 시간을 비교한다.

파일은 시험용 생성기(tests/test_files.py)로 만든 가상 문서와 1MB CSV. 형식마다 5번씩 재고 중앙값을 쓴다.
OpenAI·판단 모델을 쓰지 않는다(글자 뽑기만).

실행: uv run python bench/run_file_isolation.py
"""
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from gateway.files.extract import extract  # noqa: E402
from gateway.files.isolate import extract_isolated  # noqa: E402
from tests.test_files import make_docx, make_pdf, make_xlsx  # noqa: E402

RUNS = 5


def median_ms(fn, name: str, data: bytes) -> float:
    times = []
    for _ in range(RUNS):
        t0 = time.perf_counter()
        fn(name, data)
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times)


def main() -> None:
    big_csv = ("이름,전화,메모\n" + "김민수,010-1234-5678,배송 문의 메모입니다\n" * 25_000).encode("utf-8")
    samples = [("고객기록.docx", make_docx()), ("고객명단.xlsx", make_xlsx()),
               ("안내문.pdf", make_pdf("Contact 010-9876-5432")), ("명단.csv", "이름,전화\n김민수,010-1234-5678\n".encode()),
               ("큰명단.csv", big_csv)]
    print(f"| 파일 | 크기 | 프로세스 안 | 별도 프로세스 | 추가 |\n|---|---|---|---|---|")
    for name, data in samples:
        inside, outside = median_ms(extract, name, data), median_ms(extract_isolated, name, data)
        print(f"| {name} | {len(data) / 1024:,.0f}KB | {inside:,.0f}ms | {outside:,.0f}ms | +{outside - inside:,.0f}ms |")


if __name__ == "__main__":
    main()
