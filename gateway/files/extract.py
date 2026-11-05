"""파일에서 글자를 뽑는다 (#50). 이 PC 안에서만 처리하고 밖으로 보내지 않는다.

지원: PDF(글자 층), DOCX(문단·표·머리글·바닥글), XLSX(셀 값), TXT·CSV(UTF-8 또는 CP949)
- 위치를 함께 돌려준다: PDF는 쪽, DOCX는 문단·표, XLSX는 시트·셀, TXT는 줄 → 안내에 "3쪽", "'고객' 시트 B2"
- 글자가 없으면(스캔 PDF·이미지만 든 파일) 검사할 수 없다고 알린다. OCR은 범위 밖이다 (D19 보완)
- 안전
  - 크기 상한, 파일 앞부분으로 형식 확인(이름만 바꾼 파일을 깊이 읽지 않는다)
  - docx·xlsx는 zip이라 압축 폭탄을 검사한다: 풀린 크기 합, 압축률, 항목 수
  - XML 외부 엔티티를 풀지 않는다: python-docx는 resolve_entities=False, openpyxl은 defusedxml을 쓴다
  - 쪽·셀·글자 수 상한
"""
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024          # 올릴 수 있는 파일 크기
MAX_UNZIPPED = 100 * 1024 * 1024      # docx·xlsx를 풀었을 때 크기 합
MAX_RATIO = 100                       # 풀린 크기 ÷ 파일 크기
MAX_ZIP_ENTRIES = 5_000
MAX_PAGES = 500
MAX_CELLS = 200_000
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
    if sum(len(s.text) for s in segments) > MAX_CHARS:
        raise FileError("too_long", f"글자가 너무 많습니다 ({MAX_CHARS:,}자까지)")
    return segments


def _pdf(data: bytes) -> list[Segment]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    if not data.startswith(b"%PDF"):
        raise FileError("unreadable", "PDF 파일이 아닙니다.")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise FileError("encrypted", "암호가 걸린 PDF는 검사할 수 없습니다.")
        if len(reader.pages) > MAX_PAGES:
            raise FileError("too_long", f"쪽 수가 너무 많습니다 ({MAX_PAGES}쪽까지)")
        return [Segment(f"{i}쪽", page.extract_text() or "") for i, page in enumerate(reader.pages, 1)]
    except FileError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as e:
        raise FileError("unreadable", f"PDF를 읽지 못했습니다 ({type(e).__name__}).") from e


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
    from openpyxl.xml import DEFUSEDXML
    if not DEFUSEDXML:       # defusedxml 없이 엑셀 XML을 읽지 않는다 (외부 엔티티·엔티티 폭탄)
        raise FileError("unreadable", "안전한 XML 읽기(defusedxml)를 쓸 수 없어 엑셀 파일을 검사하지 않습니다.")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise FileError("unreadable", f"엑셀 파일을 읽지 못했습니다 ({type(e).__name__}).") from e
    out, cells = [], 0
    try:
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    if cell.value is None:
                        continue
                    cells += 1
                    if cells > MAX_CELLS:
                        raise FileError("too_long", f"셀이 너무 많습니다 ({MAX_CELLS:,}개까지)")
                    out.append(Segment(f"'{ws.title}' 시트 {cell.coordinate}", str(cell.value)))
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
