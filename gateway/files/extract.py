"""파일에서 글자를 뽑는다 (#50). 이 PC 안에서만 처리하고 밖으로 보내지 않는다.

지원: PDF(글자 층), DOCX(문단·표·머리글·바닥글), XLSX(셀 값), TXT·CSV(UTF-8 또는 CP949)
- 위치를 함께 돌려준다: PDF는 쪽, DOCX는 문단·표, XLSX는 시트·셀, TXT는 줄 → 안내에 "3쪽", "'고객' 시트 B2"
- 글자가 없으면(스캔 PDF·이미지만 든 파일) 검사할 수 없다고 알린다. OCR은 범위 밖이다 (D19 보완)
- 안전
  - 크기 상한, 파일 앞부분으로 형식 확인(이름만 바꾼 파일을 깊이 읽지 않는다)
  - docx·xlsx는 zip이라 압축 폭탄을 검사한다: 풀린 크기 합, 압축률, 항목 수
  - XML 외부 엔티티를 풀지 않는다: python-docx는 resolve_entities=False, openpyxl은 defusedxml을 쓴다
  - PDF 안의 압축된 데이터도 풀린 크기에 상한을 둔다 (pypdf 설정, 이 함수 안에서만)
  - 쪽·셀·글자 수 상한은 다 읽은 뒤가 아니라 **읽는 도중에** 확인한다. 엑셀은 빈 칸·빈 행도 센다
"""
import io
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024          # 올릴 수 있는 파일 크기
MAX_UNZIPPED = 100 * 1024 * 1024      # docx·xlsx를 풀었을 때 크기 합
MAX_RATIO = 100                       # 풀린 크기 ÷ 파일 크기
MAX_ZIP_ENTRIES = 5_000
MAX_STREAM_BYTES = 20 * 1024 * 1024   # PDF 안의 압축된 데이터 하나를 풀었을 때 크기 (pypdf 기본값 75MB)
MAX_PDF_SECONDS = 30                  # PDF 글자 뽑기 시간 (쪽을 넘길 때마다 확인)
MAX_PAGES = 500
MAX_CELLS = 200_000                   # 엑셀에서 도는 칸 수 (빈 칸 포함, 빈 행은 1칸)
MAX_CHARS = 2_000_000
SUPPORTED = (".pdf", ".docx", ".xlsx", ".txt", ".csv")


@dataclass
class Segment:
    where: str      # 위치: "3쪽", "'고객' 시트 B2", "12번째 문단"
    text: str


class FileError(ValueError):
    """검사할 수 없는 파일. code: unsupported | too_large | unreadable | encrypted | zip_bomb | no_text | too_long"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def extract(filename: str, data: bytes) -> list[Segment]:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise FileError("unsupported", f"지원하지 않는 형식입니다. 지원 형식: {', '.join(SUPPORTED)}")
    if len(data) > MAX_BYTES:
        raise FileError("too_large", f"파일이 너무 큽니다 ({MAX_BYTES // 1024 // 1024}MB까지)")
    if ext == ".pdf":
        segments = _pdf(data)
    elif ext in (".docx", ".xlsx"):
        _check_zip(data)
        segments = _docx(data) if ext == ".docx" else _xlsx(data)
    else:
        segments = _text(data)
    segments = [s for s in segments if s.text.strip()]
    if not segments:
        raise FileError("no_text", "파일에서 글자를 읽을 수 없습니다. 스캔한 이미지라면 검사할 수 없습니다.")
    if sum(len(s.text) for s in segments) > MAX_CHARS:     # docx·txt (pdf·xlsx는 읽는 도중에도 확인한다)
        raise _too_many_chars()
    return segments


def _too_many_chars() -> FileError:
    return FileError("too_long", f"글자가 너무 많습니다 ({MAX_CHARS:,}자까지)")


def _pdf_limits() -> dict[str, int]:
    """pypdf가 압축된 데이터를 풀 때 쓰는 상한 (pypdf 6.x Configuration 필드). 기본값보다 낮춘다."""
    return {
        "maximum_declared_stream_length": MAX_STREAM_BYTES,
        "array_based_stream_maximum_output_length": MAX_STREAM_BYTES,
        "zlib_maximum_output_length": MAX_STREAM_BYTES,
        "lzw_maximum_output_length": MAX_STREAM_BYTES,
        "run_length_maximum_output_length": MAX_STREAM_BYTES,
        "brotli_maximum_output_length": MAX_STREAM_BYTES,
        "stream_decoding_work_maximum_length": 4 * MAX_STREAM_BYTES,   # 필터를 여러 겹 쓴 경우의 합
    }


def _pdf(data: bytes) -> list[Segment]:
    from pypdf import PdfReader, apply_configuration
    from pypdf.errors import LimitReachedError, PyPdfError
    if not data.startswith(b"%PDF"):
        raise FileError("unreadable", "PDF 파일이 아닙니다.")
    out, chars, deadline = [], 0, time.monotonic() + MAX_PDF_SECONDS
    try:
        with apply_configuration(**_pdf_limits()):   # 이 블록 안에서만 적용된다 (contextvar)
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(""):
                raise FileError("encrypted", "암호가 걸린 PDF는 검사할 수 없습니다.")
            if len(reader.pages) > MAX_PAGES:
                raise FileError("too_long", f"쪽 수가 너무 많습니다 ({MAX_PAGES}쪽까지)")
            for i, page in enumerate(reader.pages, 1):
                if time.monotonic() > deadline:
                    raise FileError("too_long", f"PDF를 읽는 데 너무 오래 걸립니다 ({MAX_PDF_SECONDS}초까지)")
                text = page.extract_text() or ""
                chars += len(text)
                if chars > MAX_CHARS:                # 남은 쪽을 더 읽지 않는다
                    raise _too_many_chars()
                out.append(Segment(f"{i}쪽", text))
    except FileError:
        raise
    except LimitReachedError as e:                   # PdfReadError가 아니라 따로 잡아야 한다
        raise FileError("zip_bomb", "압축을 풀면 지나치게 커지는 PDF라 검사하지 않습니다.") from e
    except (PyPdfError, ValueError, KeyError, TypeError) as e:
        raise FileError("unreadable", f"PDF를 읽지 못했습니다 ({type(e).__name__}).") from e
    return out


def _check_zip(data: bytes) -> None:
    if not data.startswith(b"PK"):
        raise FileError("unreadable", "워드·엑셀 파일이 아닙니다.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
    except zipfile.BadZipFile as e:
        raise FileError("unreadable", "파일이 손상됐습니다.") from e
    unzipped = sum(i.file_size for i in infos)
    if len(infos) > MAX_ZIP_ENTRIES or unzipped > MAX_UNZIPPED or unzipped > MAX_RATIO * max(len(data), 1):
        raise FileError("zip_bomb", "압축을 풀면 지나치게 커지는 파일이라 검사하지 않습니다.")


def _docx(data: bytes) -> list[Segment]:
    import docx
    try:
        d = docx.Document(io.BytesIO(data))
    except Exception as e:        # python-docx는 손상된 파일에서 여러 종류의 예외를 낸다
        raise FileError("unreadable", f"워드 파일을 읽지 못했습니다 ({type(e).__name__}).") from e
    out = [Segment(f"{i}번째 문단", p.text) for i, p in enumerate(d.paragraphs, 1)]
    for t, table in enumerate(d.tables, 1):
        for r, row in enumerate(table.rows, 1):
            out.append(Segment(f"표 {t} {r}행", " | ".join(c.text for c in row.cells)))
    for s, section in enumerate(d.sections, 1):   # 머리글·바닥글에 담당자 연락처가 흔히 들어간다
        for part, name in ((section.header, "머리글"), (section.footer, "바닥글")):
            out += [Segment(f"{name}(구역 {s})", p.text) for p in part.paragraphs]
    return out


def _xlsx(data: bytes) -> list[Segment]:
    import openpyxl
    from openpyxl.utils import get_column_letter
    from openpyxl.xml import DEFUSEDXML
    if not DEFUSEDXML:       # defusedxml 없이 엑셀 XML을 읽지 않는다 (외부 엔티티·엔티티 폭탄)
        raise FileError("unreadable", "안전한 XML 읽기(defusedxml)를 쓸 수 없어 엑셀 파일을 검사하지 않습니다.")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise FileError("unreadable", f"엑셀 파일을 읽지 못했습니다 ({type(e).__name__}).") from e
    out, visited, chars = [], 0, 0
    try:
        for ws in wb.worksheets:
            # 파일이 적어 둔 시트 크기(A1:XFD1048576 같은 값)를 믿지 않는다. 그 크기만큼 빈 칸을 만들어 돌게 된다
            ws.reset_dimensions()
            for r, row in enumerate(ws.iter_rows(values_only=True), 1):
                visited += max(len(row), 1)        # 빈 칸·빈 행도 센다: 값이 없어도 도는 시간은 든다
                if visited > MAX_CELLS:
                    raise FileError("too_long", f"셀이 너무 많습니다 ({MAX_CELLS:,}칸까지, 빈 칸 포함)")
                for c, value in enumerate(row, 1):
                    if value is None:
                        continue
                    text = str(value)
                    chars += len(text)
                    if chars > MAX_CHARS:
                        raise _too_many_chars()
                    out.append(Segment(f"'{ws.title}' 시트 {get_column_letter(c)}{r}", text))
    finally:
        wb.close()
    return out


def _text(data: bytes) -> list[Segment]:
    for encoding in ("utf-8-sig", "cp949"):        # 한국어 엑셀이 저장한 CSV는 CP949인 경우가 많다
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise FileError("unreadable", "글자 인코딩을 알 수 없습니다 (UTF-8 또는 CP949).")
    return [Segment(f"{i}번째 줄", line) for i, line in enumerate(text.splitlines(), 1)]
