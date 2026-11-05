"""파일 검사 (#50). 가상 개인정보가 든 파일을 테스트 안에서 만들어 검사한다. 결과·응답에 원본 값이 없어야 한다."""
import base64
import io
import zipfile
import zlib

import docx
import httpx
import openpyxl
import pypdf
import pytest
from fastapi.testclient import TestClient

from gateway.files import extract as extract_module
from gateway.files.extract import FileError, extract
from gateway.files.scan import scan, summary
from gateway.main import create_app
from gateway.masking.masker import RuleMasker
from gateway.storage.log import RequestLog
from tests.fakes import FakeUpstream, test_settings

RAW = ("김민수", "010-1234-5678", "jiwoo.han@example.com", "M12345678", "010-2222-3333")


@pytest.fixture
def masker(tmp_path):
    return RuleMasker.from_config(test_settings(tmp_path).config.masking)   # 쇼핑몰 프리셋 + 시연용 사전


def make_docx() -> bytes:
    d = docx.Document()
    d.add_paragraph("고객 응대 기록")
    d.add_paragraph("김민수 고객 연락처 010-1234-5678")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "이메일", "jiwoo.han@example.com"
    d.sections[0].header.paragraphs[0].text = "담당 최유진 010-2222-3333"
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def make_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "고객"
    for row in (["이름", "전화", "회원번호"], ["김민수", "010-1234-5678", "M12345678"], ["이서연", "010-1234-5678", ""]):
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_pdf(*pages: str | bytes | None, flate: bool = False, form: bytes | None = None) -> bytes:
    """쪽마다 글자 층이 있는(str) 또는 없는(None, 스캔 PDF처럼) PDF. bytes는 쪽 내용 스트림 그대로.
    표준 글꼴이라 영문·숫자만 쓴다. flate=True면 쪽 내용을 zlib으로 압축한다 (압축 폭탄 시험용).
    form을 주면 모든 쪽에서 /X0 Do로 불러 쓸 수 있는 양식 객체(XObject)가 된다."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    resources = b"/Font << /F1 3 0 R >>"
    if form is not None:
        objs.append(b"<< /Type /XObject /Subtype /Form /BBox [0 0 300 200] /Resources << /Font << /F1 3 0 R >> >> "
                    b"/Length %d >>\nstream\n" % len(form) + form + b"\nendstream")
        resources += b" /XObject << /X0 4 0 R >>"
    kids = []
    for text in pages:
        kids.append(len(objs) + 1)
        if text is None:
            objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] >>")
            continue
        stream = text if isinstance(text, bytes) else f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("latin-1")
        extra = b""
        if flate:
            stream, extra = zlib.compress(stream, 9), b" /Filter /FlateDecode"
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents %d 0 R "
                    b"/Resources << %s >> >>" % (len(objs) + 2, resources))
        objs.append(b"<< /Length %d%s >>\nstream\n" % (len(stream), extra) + stream + b"\nendstream")
    objs[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % k for k in kids), len(pages))
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


def test_docx_finds_body_table_and_header(masker):
    r = scan("고객기록.docx", make_docx(), masker)
    assert r.counts == {"고객": 1, "전화번호": 2, "이메일": 1, "직원": 1}
    wheres = {f.where for f in r.findings}
    assert {"2번째 문단", "표 1 1행", "머리글(구역 1)"} <= wheres     # 머리글의 담당자 연락처도 찾는다
    assert "[고객1] 고객 연락처 [전화번호1]" in r.masked_text


def test_xlsx_cells_with_locations_and_same_value_same_token(masker):
    r = scan("고객명단.xlsx", make_xlsx(), masker)
    assert r.counts == {"고객": 2, "전화번호": 1, "회원번호": 1}     # 같은 전화번호 두 칸 → 값 1개
    assert {f.where for f in r.findings} >= {"'고객' 시트 A2", "'고객' 시트 B2", "'고객' 시트 C2", "'고객' 시트 B3"}
    assert r.masked_text.count("[전화번호1]") == 2


def test_pdf_text_layer(masker):
    r = scan("안내문.pdf", make_pdf("Contact 010-9876-5432 or a.b@example.com"), masker)
    assert r.counts == {"전화번호": 1, "이메일": 1} and r.findings[0].where == "1쪽"


def test_cp949_csv(masker):
    r = scan("명단.csv", "이름,전화\n김민수,010-1234-5678\n".encode("cp949"), masker)
    assert r.counts == {"고객": 1, "전화번호": 1} and r.findings[0].where == "2번째 줄"


def test_report_never_contains_raw_values(masker):
    for name, data in (("a.docx", make_docx()), ("b.xlsx", make_xlsx())):
        r = scan(name, data, masker)
        text = summary(r) + r.masked_text + "".join(f.where for f in r.findings)
        for value in RAW:
            assert value not in text
        assert "개인정보" in summary(r) and "가명화된 텍스트" in summary(r)


@pytest.mark.parametrize("name, data, code", [
    ("스캔.pdf", make_pdf(None), "no_text"),                       # 글자 층이 없다 (OCR은 범위 밖)
    ("문서.hwp", b"anything", "unsupported"),
    ("가짜.pdf", b"hello", "unreadable"),                          # 이름만 바꾼 파일
    ("가짜.docx", b"hello", "unreadable"),
], ids=["scanned-pdf", "unsupported", "fake-pdf", "fake-docx"])
def test_files_that_cannot_be_scanned(name, data, code):
    with pytest.raises(FileError) as e:
        extract(name, data)
    assert e.value.code == code


def test_zip_bomb_is_refused_before_parsing():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"0" * 5_000_000)            # 5MB가 수 KB로 줄어든다 (압축률 > 100)
    with pytest.raises(FileError) as e:
        extract("폭탄.docx", buf.getvalue())
    assert e.value.code == "zip_bomb"


# ── 읽는 도중의 상한 (보안 검토: 다 읽은 뒤에 세면 그 전에 시간·메모리가 먼저 든다) ──

def _patch_zip(data: bytes, name: str, fn) -> bytes:
    """zip 안 파일 하나의 내용을 fn으로 바꾼다. 압축률 검사에 먼저 걸리지 않게 압축하지 않고 저장한다."""
    src, dst = zipfile.ZipFile(io.BytesIO(data)), io.BytesIO()
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_STORED) as z:
        for info in src.infolist():
            body = src.read(info)
            z.writestr(info.filename, fn(body) if info.filename == name else body)
    return dst.getvalue()


def _between(body: bytes, start: bytes, end: bytes, new: bytes) -> bytes:
    i = body.index(start) + len(start)
    return body[:i] + new + body[body.index(end, i):]


def _set_dimension(xlsx: bytes, ref: str) -> bytes:
    """시트 XML에 적힌 크기(<dimension ref=…>)만 바꾼다. 실제 칸은 그대로."""
    return _patch_zip(xlsx, "xl/worksheets/sheet1.xml", lambda b: _between(b, b'<dimension ref="', b'"', ref.encode()))


def test_xlsx_ignores_declared_sheet_size(monkeypatch):
    """A1:XFD1048576(약 170억 칸)이라고 적힌 시트도 실제로 있는 칸만 돈다.
    적힌 크기를 믿으면 한 행이 16,384칸이 되어 상한(1,000칸)을 첫 행에서 넘는다."""
    monkeypatch.setattr(extract_module, "MAX_CELLS", 1_000)
    segments = extract("큰척.xlsx", _set_dimension(make_xlsx(), "A1:XFD1048576"))
    assert [s.where for s in segments][:3] == ["'고객' 시트 A1", "'고객' 시트 B1", "'고객' 시트 C1"]


def test_xlsx_counts_empty_rows_toward_cell_cap():
    wb = openpyxl.Workbook()
    wb.active["A1"], wb.active["A1000000"] = "이름", "x"       # 사이의 빈 행 100만 개
    buf = io.BytesIO()
    wb.save(buf)
    with pytest.raises(FileError) as e:
        extract("빈행.xlsx", buf.getvalue())
    assert e.value.code == "too_long"


def test_pdf_char_cap_stops_before_reading_remaining_pages(monkeypatch):
    calls = []
    original = pypdf.PageObject.extract_text
    monkeypatch.setattr(pypdf.PageObject, "extract_text", lambda self, *a, **k: calls.append(1) or original(self, *a, **k))
    monkeypatch.setattr(extract_module, "MAX_CHARS", 10)
    with pytest.raises(FileError) as e:
        extract("긴문서.pdf", make_pdf("A" * 50, "B" * 50, "C" * 50))
    assert e.value.code == "too_long" and len(calls) == 1          # 1쪽에서 넘었으니 2·3쪽은 읽지 않는다


def test_pdf_decompression_cap(monkeypatch):
    monkeypatch.setattr(extract_module, "MAX_STREAM_BYTES", 1_000)
    data = make_pdf("0" * 50_000, flate=True)                       # 수십 바이트가 5만 바이트로 풀린다
    assert len(data) < 2_000
    with pytest.raises(FileError) as e:
        extract("폭탄.pdf", data)
    assert e.value.code == "zip_bomb"


def test_pdf_time_cap_checked_between_pages(monkeypatch):
    monkeypatch.setattr(extract_module, "MAX_PDF_SECONDS", -1)      # 이미 시간을 다 쓴 상태
    with pytest.raises(FileError) as e:
        extract("느린.pdf", make_pdf("Contact 010-9876-5432"))
    assert e.value.code == "too_long"


def _ticking_clock(monkeypatch) -> list:
    """부를 때마다 1초가 지나는 시계. 몇 번 불렸는지 = 몇 번 확인했는지."""
    calls = []
    monkeypatch.setattr(extract_module, "_clock", lambda: calls.append(1) or len(calls))
    return calls


def test_pdf_time_cap_checked_inside_a_page(monkeypatch):
    calls = _ticking_clock(monkeypatch)
    monkeypatch.setattr(extract_module, "MAX_PDF_SECONDS", 50)
    with pytest.raises(FileError) as e:
        extract("느린쪽.pdf", make_pdf(b"q Q " * 1_000))                # 글자 없는 연산자 2,000개짜리 한 쪽
    assert e.value.code == "too_long" and len(calls) < 60           # 연산자마다 확인해서 쪽 중간에 멈춘다


def test_pdf_char_cap_checked_inside_a_page(monkeypatch):
    calls = _ticking_clock(monkeypatch)
    monkeypatch.setattr(extract_module, "MAX_PDF_SECONDS", 10**9)
    monkeypatch.setattr(extract_module, "MAX_CHARS", 30)
    block = b"BT /F1 12 Tf 20 100 Td (AAAAAAAAAA) Tj ET\n"               # 연산자 5개, 10자
    with pytest.raises(FileError) as e:
        extract("긴쪽.pdf", make_pdf(block * 200))                      # 한 쪽에 2,000자
    assert e.value.code == "too_long"
    assert 10 < len(calls) < 100        # 연산자마다 확인했고(쪽이 끝난 뒤 한 번이 아니라), 1,000개를 다 돌기 전에 멈췄다


def test_pdf_time_cap_survives_pypdf_swallowing_errors_inside_forms(monkeypatch):
    """한 양식 객체를 500번 불러 쓰는 쪽. pypdf는 양식 안에서 난 예외를 삼키지만, 바깥 쪽의 다음 연산자에서 다시 걸린다."""
    calls = _ticking_clock(monkeypatch)
    monkeypatch.setattr(extract_module, "MAX_PDF_SECONDS", 50)
    with pytest.raises(FileError) as e:
        extract("양식반복.pdf", make_pdf(b"/X0 Do " * 500, form=b"q Q " * 20))   # 양식 하나에 연산자 40개
    assert e.value.code == "too_long" and len(calls) < 100           # 양식 500번(연산자 2만 개)을 다 돌지 않았다


def _sheet_rows(rows: bytes) -> bytes:
    return _patch_zip(make_xlsx(), "xl/worksheets/sheet1.xml", lambda b: _between(b, b"<sheetData>", b"</sheetData>", rows))


def test_xlsx_row_wider_than_xfd_refused_before_parsing(monkeypatch):
    """주소 없는 칸 2만 개짜리 한 행. 파서는 한 행을 통째로 만든 뒤 넘겨 주므로 그 전에 막아야 한다."""
    opened, original = [], openpyxl.load_workbook
    monkeypatch.setattr(openpyxl, "load_workbook", lambda *a, **k: opened.append(1) or original(*a, **k))
    with pytest.raises(FileError) as e:
        extract("넓은행.xlsx", _sheet_rows(b'<row r="1">' + b"<c><v>1</v></c>" * 20_000 + b"</row>"))
    assert e.value.code == "unreadable" and opened == []           # 엑셀 파서까지 가지 않았다


def test_xlsx_cell_address_past_xfd_refused():
    """칸은 하나지만 주소가 ZZZ1(18,278열)이다. 엑셀에는 없는 열이다."""
    with pytest.raises(FileError) as e:
        extract("먼열.xlsx", _sheet_rows(b'<row r="1"><c r="ZZZ1"><v>1</v></c></row>'))
    assert e.value.code == "unreadable"


def test_xml_element_cap_applies_to_docx(monkeypatch):
    monkeypatch.setattr(extract_module, "MAX_XML_ELEMENTS", 50)
    with pytest.raises(FileError) as e:
        extract("고객기록.docx", make_docx())
    assert e.value.code == "too_long"


def _add_to_zip(data: bytes, name: str, body: bytes) -> bytes:
    src, dst = zipfile.ZipFile(io.BytesIO(data)), io.BytesIO()
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_STORED) as z:
        for info in src.infolist():
            z.writestr(info.filename, src.read(info))
        z.writestr(name, body)
    return dst.getvalue()


def _xml_elements(data: bytes) -> int:
    return extract_module._count_xml(data, zipfile.ZipFile(io.BytesIO(data)).infolist()).elements


def test_utf16_xml_without_bom_is_counted(monkeypatch):
    """앞이 "\\0<"(BOM 없는 UTF-16BE)이고 이름도 .xml이 아닌 부분. expat은 이것을 XML로 읽으므로 세야 한다."""
    data = _add_to_zip(make_docx(), "word/media/x.bin", ("<a>" + "<b/>" * 50 + "</a>").encode("utf-16-be"))
    monkeypatch.setattr(extract_module, "MAX_XML_ELEMENTS", _xml_elements(make_docx()) + 10)
    with pytest.raises(FileError) as e:
        extract("utf16.docx", data)
    assert e.value.code == "too_long"


def test_xml_after_long_leading_whitespace_is_counted(monkeypatch):
    """선언 없는 XML은 앞에 공백이 얼마든 와도 된다. 앞 64바이트만 보면 놓친다."""
    body = b" " * 200 + b"<a>" + b"<b/>" * 50 + b"</a>"
    data = _add_to_zip(make_docx(), "word/media/x.bin", body)
    monkeypatch.setattr(extract_module, "MAX_XML_ELEMENTS", _xml_elements(make_docx()) + 10)
    with pytest.raises(FileError) as e:
        extract("앞공백.docx", data)
    assert e.value.code == "too_long"


def test_extensionless_part_uses_empty_extension():
    """확장자가 없는 부분의 확장자는 빈 값이다(OPC). 이름 전체를 확장자로 보면 형식 목록과 어긋난다."""
    def declare(body: bytes) -> bytes:
        return body.replace(b"</Types>", b'<Default Extension="" ContentType="application/xml"/></Types>')
    data = _patch_zip(_add_to_zip(make_docx(), "word/noext", b"not xml"), "[Content_Types].xml", declare)
    with pytest.raises(FileError) as e:
        extract("확장자없음.docx", data)
    assert e.value.code == "unreadable"


def test_part_declared_xml_in_content_types_must_parse():
    def declare(body: bytes) -> bytes:
        return body.replace(b"</Types>", b'<Override PartName="/word/custom.dat" ContentType="application/xml"/></Types>')
    data = _patch_zip(_add_to_zip(make_docx(), "word/custom.dat", b"not xml"), "[Content_Types].xml", declare)
    with pytest.raises(FileError) as e:
        extract("형식목록.docx", data)
    assert e.value.code == "unreadable"                              # XML이라고 적혀 있는데 훑지 못했다 → 읽지 않는다


def test_binary_parts_are_not_parsed_as_xml():
    """이미지·인쇄 설정 같은 이진 부분은 건너뛴다 (정상 파일을 막지 않는다)."""
    data = _add_to_zip(make_xlsx(), "xl/printerSettings/printerSettings1.bin", "HP LaserJet".encode("utf-16-le"))
    data = _add_to_zip(data, "xl/media/image1.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    assert any(s.where == "'고객' 시트 A2" for s in extract("인쇄설정.xlsx", data))


def test_pdf_char_cap_counts_text_pypdf_has_not_flushed_yet(monkeypatch):
    """BT 하나 안의 Tj 200개. pypdf는 ET에서야 글자를 넘겨 주므로, 연산자의 문자열로 미리 세야 중간에 멈춘다."""
    calls = _ticking_clock(monkeypatch)
    monkeypatch.setattr(extract_module, "MAX_PDF_SECONDS", 10**9)
    monkeypatch.setattr(extract_module, "MAX_CHARS", 30)
    with pytest.raises(FileError) as e:
        extract("모아두기.pdf", make_pdf(b"BT /F1 12 Tf 20 100 Td " + b"(AAAAAAAAAA) Tj " * 200 + b"ET"))
    assert e.value.code == "too_long" and len(calls) < 20             # Tj 4개째에서 멈춘다


def test_pdf_page_text_bytes_cap(monkeypatch):
    """글자 대응표가 코드 하나를 최대 256글자로 늘릴 수 있으므로, 쪽마다 문자열 바이트에 상한을 둔다."""
    monkeypatch.setattr(extract_module, "MAX_PAGE_TEXT_BYTES", 100)
    with pytest.raises(FileError) as e:
        extract("빽빽.pdf", make_pdf(b"BT /F1 12 Tf 20 100 Td " + b"(AAAAAAAAAA) Tj " * 50 + b"ET"))   # 500바이트
    assert e.value.code == "too_long"
    assert extract("보통.pdf", make_pdf("A" * 50, "B" * 50))       # 쪽마다 따로 센다 (50 + 50바이트)


def test_pdf_page_text_bytes_cap_survives_swallowing_inside_forms(monkeypatch):
    """양식 안에서 상한에 걸리면 pypdf가 삼킨다. 쪽의 나머지가 글자 연산자가 아니어도 멈춰야 한다."""
    monkeypatch.setattr(extract_module, "MAX_PAGE_TEXT_BYTES", 100)
    form = b"BT /F1 12 Tf 20 100 Td " + b"(AAAAAAAAAA) Tj " * 20 + b"ET"         # 양식 하나에 200바이트
    with pytest.raises(FileError) as e:
        extract("양식글자.pdf", make_pdf(b"/X0 Do q Q", form=form))
    assert e.value.code == "too_long"


def test_xml_entity_declaration_refused():
    def add_entity(body: bytes) -> bytes:
        end = body.index(b"?>") + 2                                  # <?xml …?> 뒤
        return body[:end] + b'<!DOCTYPE w:document [<!ENTITY a "b">]>' + body[end:]
    with pytest.raises(FileError) as e:
        extract("엔티티.docx", _patch_zip(make_docx(), "word/document.xml", add_entity))
    assert e.value.code == "unreadable"


# ── API ──────────────────────────────────────────────────────

def post(client, name, data, **kw):
    return client.post("/v1/files/scan", json={"filename": name, "content_base64": base64.b64encode(data).decode()}, **kw)


def test_api_scan_returns_report_without_values(tmp_path):
    up = FakeUpstream()
    app = create_app(test_settings(tmp_path), transport=httpx.MockTransport(up), log=RequestLog(tmp_path / "g.db"))
    with TestClient(app) as client:
        r = post(client, "고객명단.xlsx", make_xlsx())
        bad = client.post("/v1/files/scan", json={"filename": "a.xlsx", "content_base64": "@@@"})
        hwp = post(client, "a.hwp", b"x")
    assert r.status_code == 200 and r.json()["has_pii"] is True
    assert r.json()["counts"] == {"고객": 2, "전화번호": 1, "회원번호": 1}
    for value in RAW:
        assert value not in r.text
    assert up.requests == []                                       # 파일은 밖으로 나가지 않는다 (판단 API도 안 부른다)
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, "invalid_file")
    assert (hwp.status_code, hwp.json()["error"]["code"]) == (400, "unsupported")


def test_api_scan_follows_gateway_key_rules(tmp_path):
    app = create_app(test_settings(tmp_path), transport=httpx.MockTransport(FakeUpstream()),
                     log=RequestLog(tmp_path / "g.db"))
    with TestClient(app, client=("203.0.113.5", 50000)) as client:  # 키 없이 다른 PC에서
        assert post(client, "a.csv", b"x").status_code == 401
