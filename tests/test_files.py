"""파일 검사 (#50). 가상 개인정보가 든 파일을 테스트 안에서 만들어 검사한다. 결과·응답에 원본 값이 없어야 한다."""
import base64
import io
import zipfile

import docx
import httpx
import openpyxl
import pytest
from fastapi.testclient import TestClient

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


def make_pdf(text: str | None) -> bytes:
    """글자 층이 있는(text) 또는 없는(None, 스캔 PDF처럼) 한 쪽짜리 PDF. 표준 글꼴이라 영문·숫자만 쓴다."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"]
    if text is None:
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] >>")
    else:
        stream = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("latin-1")
        objs += [b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R "
                 b"/Resources << /Font << /F1 5 0 R >> >> >>",
                 b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
                 b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
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
