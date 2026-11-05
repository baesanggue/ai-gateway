"""파일에서 글자를 뽑는다 (#50). 이 PC 안에서만 처리하고 밖으로 보내지 않는다.

지원: PDF(글자 층), DOCX(문단·표·머리글·바닥글), XLSX(셀 값), TXT·CSV(UTF-8 또는 CP949)
- 위치를 함께 돌려준다: PDF는 쪽, DOCX는 문단·표, XLSX는 시트·셀, TXT는 줄 → 안내에 "3쪽", "'고객' 시트 B2"
- 글자가 없으면(스캔 PDF·이미지만 든 파일) 검사할 수 없다고 알린다. OCR은 범위 밖이다 (D19 보완)
- 안전
  - 크기 상한, 파일 앞부분으로 형식 확인(이름만 바꾼 파일을 깊이 읽지 않는다)
  - docx·xlsx는 zip이라 압축 폭탄을 검사한다: 풀린 크기 합, 압축률, 항목 수
  - XML 외부 엔티티를 풀지 않는다: python-docx는 resolve_entities=False, openpyxl은 defusedxml을 쓴다
  - docx·xlsx 안 XML은 파싱 전에 트리를 만들지 않고 훑어서 요소 수·셀 수·한 행의 칸 수를 센다
    (파서는 한 행을 통째로 메모리에 올린 뒤 넘겨 주므로, 넘겨받은 뒤에 세면 늦다)
  - PDF 안의 압축된 데이터도 풀린 크기에 상한을 둔다 (pypdf 설정, 이 함수 안에서만)
  - 쪽·셀·글자 수·시간 상한은 다 읽은 뒤가 아니라 **읽는 도중에** 확인한다
    - PDF는 pypdf가 연산자·글자 조각마다 부르는 함수로 한 쪽 안에서도 본다
    - 엑셀은 빈 칸·빈 행도 센다
"""
import io
import time
import xml.sax.handler
import zipfile
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024          # 올릴 수 있는 파일 크기
MAX_UNZIPPED = 100 * 1024 * 1024      # docx·xlsx를 풀었을 때 크기 합
MAX_RATIO = 100                       # 풀린 크기 ÷ 파일 크기
MAX_ZIP_ENTRIES = 5_000
MAX_XML_ELEMENTS = 1_500_000          # docx·xlsx 안 XML 요소 수 합 (셀 20만 개짜리 엑셀이 약 85만)
MAX_STREAM_BYTES = 20 * 1024 * 1024   # PDF 안의 압축된 데이터 하나를 풀었을 때 크기 (pypdf 기본값 75MB)
MAX_PDF_SECONDS = 30                  # PDF 글자 뽑기 시간 (연산자마다 확인)
MAX_PAGES = 500
MAX_CELLS = 200_000                   # 엑셀에서 도는 칸 수 (빈 칸 포함, 빈 행은 1칸)
MAX_COLUMNS = 16_384                  # 엑셀의 마지막 열 XFD
MAX_CHARS = 2_000_000
SUPPORTED = (".pdf", ".docx", ".xlsx", ".txt", ".csv")
_clock = time.monotonic                # 테스트에서 바꿔 끼운다


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


_SHOW_TEXT = (b"Tj", b"TJ", b"'", b'"')       # 글자를 찍는 PDF 연산자


def _shown_len(operands) -> int:
    """글자 연산자에 실린 문자열 길이 합. TJ는 [(abc) 120 (def)]처럼 배열이다."""
    n = 0
    for o in operands:
        n += sum(len(i) for i in (o if isinstance(o, list) else (o,)) if isinstance(i, (str, bytes)))
    return n


class _PdfGuard:
    """한 쪽 안에서도 시간·글자 상한을 본다. pypdf가 연산자마다 check, 글자 조각마다 count를 부른다.

    - 글자는 연산자가 실행되기 **전에** 문자열 길이로 센다. pypdf는 글자를 모아 두었다가
      위치가 바뀔 때(ET, Td 등)에야 넘겨 주므로, 넘겨받은 글자만 세면 모아 둔 양을 못 본다.
      넘겨받은 글자(count)와 둘 중 큰 쪽을 쓴다.
    - pypdf는 양식 객체(XObject) 안에서 난 예외를 삼키고 계속한다. 그래도 상태가 남아 있어
      바깥 쪽의 다음 연산자에서 다시 걸리고, 거기서는 예외가 그대로 올라온다.
    """

    def __init__(self):
        self.deadline = _clock() + MAX_PDF_SECONDS
        self.done = 0                     # 다 읽은 쪽들의 실제 글자 수
        self.shown = self.flushed = 0     # 지금 쪽: 연산자로 센 값, 넘겨받은 값

    def check(self, operator=None, operands=(), *_) -> None:
        if operator in _SHOW_TEXT:
            self.shown += _shown_len(operands)
        if _clock() > self.deadline:
            raise FileError("too_long", f"PDF를 읽는 데 너무 오래 걸립니다 ({MAX_PDF_SECONDS}초까지)")
        if self.done + max(self.shown, self.flushed) > MAX_CHARS:
            raise _too_many_chars()

    def count(self, text, *_) -> None:
        self.flushed += len(text)
        self.check()

    def page_done(self, text: str) -> None:
        self.done += len(text)            # 어림값 대신 실제 길이로 맞춘다
        self.shown = self.flushed = 0
        self.check()                      # 양식 객체 안에서 걸려 삼켜졌어도 여기서 멈춘다


def _pdf(data: bytes) -> list[Segment]:
    from pypdf import PdfReader, apply_configuration
    from pypdf.errors import LimitReachedError, PyPdfError
    if not data.startswith(b"%PDF"):
        raise FileError("unreadable", "PDF 파일이 아닙니다.")
    out, guard = [], _PdfGuard()
    try:
        with apply_configuration(**_pdf_limits()):   # 이 블록 안에서만 적용된다 (contextvar)
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(""):
                raise FileError("encrypted", "암호가 걸린 PDF는 검사할 수 없습니다.")
            if len(reader.pages) > MAX_PAGES:
                raise FileError("too_long", f"쪽 수가 너무 많습니다 ({MAX_PAGES}쪽까지)")
            for i, page in enumerate(reader.pages, 1):
                guard.check()
                text = page.extract_text(visitor_operand_before=guard.check, visitor_text=guard.count) or ""
                guard.page_done(text)
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
    _count_xml(data, infos)


CONTENT_TYPES = "[Content_Types].xml"


def _is_xml_type(content_type: str) -> bool:
    t = content_type.split(";")[0].strip().lower()
    return t.endswith("+xml") or t in ("application/xml", "text/xml")


class _XmlCounter(xml.sax.handler.ContentHandler):
    """XML 요소를 트리 없이 센다. 상한을 넘으면 그 자리에서 멈춘다.
    [Content_Types].xml을 읽을 때는 XML이라고 적힌 확장자·부분 이름도 모은다."""

    def __init__(self):
        super().__init__()
        self.elements = self.cells = self.row_cells = 0
        self.xml_extensions: set[str] = set()
        self.xml_parts: set[str] = set()

    def startElement(self, name, attrs):
        local = name.rpartition(":")[2]
        self.elements += 1
        if local == "row":
            self.row_cells = 0
        elif local == "c":                           # 엑셀 셀
            self.cells += 1
            self.row_cells += 1
        elif local in ("Default", "Override") and _is_xml_type(attrs.get("ContentType", "")):
            if local == "Default":
                self.xml_extensions.add(attrs.get("Extension", "").lower())
            else:
                self.xml_parts.add(attrs.get("PartName", "").lstrip("/").lower())
        if self.elements > MAX_XML_ELEMENTS or self.cells > MAX_CELLS:
            raise FileError("too_long", "문서 구조가 너무 커서 검사하지 않습니다.")
        if self.row_cells > MAX_COLUMNS:
            raise FileError("unreadable", f"엑셀 한 행에 칸이 {MAX_COLUMNS:,}개(XFD열)보다 많습니다.")

    def declared_xml(self, filename: str) -> bool:
        name = filename.lower()
        ext = name.rpartition("/")[2].rpartition(".")[2]
        return name.endswith((".xml", ".rels")) or ext in self.xml_extensions or name in self.xml_parts


_EBCDIC_XML_DECL = b"\x4c\x6f\xa7\x94"       # EBCDIC로 쓴 "<?xm"


def _looks_like_xml(head: bytes) -> bool:
    """XML 파서(expat, libxml2)가 문서로 읽기 시작할 수 있는 앞부분인가. 넓게 잡는다:
    BOM(UTF-8·16·32), BOM 없는 UTF-16·32("\\0<"), 앞 공백, EBCDIC."""
    return head.startswith(_EBCDIC_XML_DECL) or head.lstrip(b"\x00\xef\xbb\xbf\xfe\xff \t\r\n").startswith(b"<")


def _count_xml(data: bytes, infos: list[zipfile.ZipInfo]) -> _XmlCounter:
    """docx·xlsx 안 XML 부분을 파서가 읽기 전에 훑는다.
    - 훑는 대상: 이름이 .xml·.rels, [Content_Types].xml에 XML로 적힌 부분, 앞부분이 XML처럼 보이는 부분.
      이 중 하나라도 훑지 못하면 파일을 읽지 않는다(실패 시 닫힘). 그래야 파서는 읽는데 검사는 건너뛴 부분이 없다.
    - defusedxml이라 엔티티 선언·외부 참조가 있으면 멈춘다."""
    import defusedxml.sax
    from defusedxml import DefusedXmlException
    counter = _XmlCounter()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in sorted(infos, key=lambda i: i.filename != CONTENT_TYPES):   # 형식 목록을 먼저 읽는다
                if info.is_dir():
                    continue
                with z.open(info) as f:
                    if not (counter.declared_xml(info.filename) or _looks_like_xml(f.peek(64)[:64])):
                        continue                     # 이미지·글꼴·인쇄 설정 같은 이진 파일
                    parser = defusedxml.sax.make_parser()
                    parser.setContentHandler(counter)
                    parser.parse(f)
    except FileError:
        raise
    except (xml.sax.SAXException, DefusedXmlException, zipfile.BadZipFile, NotImplementedError, RuntimeError,
            OSError, EOFError) as e:
        raise FileError("unreadable", f"파일 안의 XML을 읽지 못했습니다 ({type(e).__name__}).") from e
    return counter


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
                if len(row) > MAX_COLUMNS:          # 셀 주소가 XFD열을 넘는다 (ZZZ1 같은 주소)
                    raise FileError("unreadable", f"엑셀 한 행에 칸이 {MAX_COLUMNS:,}개(XFD열)보다 많습니다.")
                for c, value in enumerate(row, 1):
                    if value is None:
                        continue
                    text = str(value)
                    chars += len(text)
                    if chars > MAX_CHARS:
                        raise _too_many_chars()
                    out.append(Segment(f"'{ws.title}' 시트 {get_column_letter(c)}{r}", text))
    except FileError:
        raise
    except Exception as e:        # 손상된 시트는 읽는 도중에도 여러 종류의 예외를 낸다 (500 대신 안내)
        raise FileError("unreadable", f"엑셀 파일을 읽지 못했습니다 ({type(e).__name__}).") from e
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
