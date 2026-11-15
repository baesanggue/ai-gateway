"""11/17 결과 발표 자료를 만든다 (#95). 5분, 9장: 개요 → AI 활용 내역 → 결과.

    python docs/presentation/build_deck.py      # 시스템 파이썬(python-pptx 1.0, segno 1.6)으로 실행

- 숫자는 만들 때 저장소에서 읽는다(커밋한 날, 설계 결정, AI 활용 기록, 시험 수). 발표 전날 다시 만들면 최신이 된다
- 측정 숫자는 docs/m3-numbers.md, 한계는 docs/limitations.md에서 왔다
- 시연 장에는 시연 화면 캡처를 넣어 둔다. 다시 만든 뒤에는 add_video.ps1로 30초 영상(demo-30s.mp4, 자동 재생)으로 바꾼다 (#106)
- 디자인 (11/15 새로, #126: "회색 글씨는 검정으로, 회색 선으로 나눈 상자는 안 읽힌다 — 고치지 말고 새로")
  - 상자는 '색 머리 띠 상자'(card): 위에 진한 색 띠 + 흰 굵은 제목, 아래 흰 바탕, 테두리는 띠와 같은 색 1.5pt.
    프로젝터에서 연한 색과 가는 선은 바래지만 진한 띠와 테두리는 남는다. 표는 머리 줄을 채우고, 이름표 칸을 채운 한 줄 상자(label_row)를 쓴다
  - 띠 색은 진한 파랑이 기본. 3장은 어디서 도는지를 색으로 — 이 PC 안 초록, 밖 파랑, 기존 서비스 검정. 한계는 빨강
  - 글씨는 검정. 회색 글씨는 쓰지 않는다. 강조만 파랑·초록·빨강·주황, 어두운 바탕에서는 흰색·연파랑
  - 표지·마무리는 진한 남색 바탕에 흰 글씨, 본문 장은 흰 바탕. 가명 토큰 칩([고객1], 노랑)은 제품의 상징이라 남긴다
  - 11/13(#111)의 "카드 대신 여백과 가는 선"은 되돌렸다
- 글씨 크기는 역할마다 하나 (#124): 표지 제목 54 · 큰 숫자 40 · 장 제목 30 · 소제목 20 · 본문 16(상자 제목은 굵게) · 보조 14.
  만들 때 모든 글씨의 크기와 색이 정한 것 중 하나인지 검사한다(check_text)
- 발표자 노트는 발표 대본 talk-script.md에서 읽는다(#120). 대본을 고쳤으면 이 스크립트와 add_video.ps1을 다시 돌린다
- 마지막 장에 설문 폼 QR (D30, #118). 체험판 주소가 아니라 폼으로 연결한다: 체험판 주소는 켤 때마다 바뀌지만 폼 링크는
  그대로다. 체험판 주소·비밀번호는 폼 안에만 둔다(발표 자료는 제출되므로 비밀번호를 슬라이드에 쓰지 않는다)
"""
import io
import re
import subprocess
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "2026-11-17-garimgate.pptx"
DEMO_SHOT = ROOT / "docs" / "design" / "screen-demo.png"
SCRIPT = Path(__file__).resolve().parent / "talk-script.md"     # 발표 대본 → 발표자 노트 (#120)
PRESENTER = "컴퓨터소프트웨어공학과  배상규"
SURVEY_URL = "https://docs.google.com/forms/d/e/1FAIpQLSf3bwL1mZQ2NK1rP3dD4P9aeijsQQbZRPiZAi9NlYfjQMdZPA/viewform"  # 설문 폼 (docs/survey.md)

FONT = "맑은 고딕"
MONO = "Consolas"
# 색 (#126). 글씨는 검정이 기본이고 회색 글씨는 없다
INK = "111827"                                    # 글씨 (흰 바탕 대비 17:1)
NAVY, LIGHT = "0F1E3D", "BFDBFE"                  # 표지·마무리 바탕, 그 위 보조 글씨 (대비 11:1)
BAND, BLUE, TINT = "1E40AF", "1D4ED8", "DBEAFE"   # 머리 띠·테두리(흰 글씨 8.7:1) · 강조 글씨(6.7:1) · AI 열 바탕
GREEN, RED, AMBER, DARK = "0F7B5A", "C0392B", "B45309", "1F2937"
YELLOW, WHITE = "FDE68A", "FFFFFF"
TEXT_COLORS = {INK, WHITE, LIGHT, BLUE, GREEN, RED, AMBER}   # 글씨에 쓰는 색. 모두 제 바탕에서 대비 4.5:1 이상

# 글씨 크기 (pt): 역할마다 하나 (#124)
T_COVER, T_STAT, T_TITLE, T_HEAD, T_BODY, T_SMALL = 54, 40, 30, 20, 16, 14
SIZES = {T_COVER, T_STAT, T_TITLE, T_HEAD, T_BODY, T_SMALL}

L, W = 0.75, 11.83            # 좌우 여백 0.75" → 본문 폭 (슬라이드 13.333")
TOP = 1.85                    # 본문이 시작하는 높이
PAD = 0.15                    # 상자 안 좌우 여백
BAND_H = 0.5                  # 머리 띠 높이
HANG = 0.21                   # 목록 둘째 줄 들여쓰기: 16pt '·  '의 폭


def rgb(hex_: str) -> RGBColor:
    return RGBColor.from_string(hex_)


# ── 저장소에서 읽는 숫자 ──

def repo_numbers() -> dict[str, str]:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout
    days = len(set(git("log", "--format=%ad", "--date=short").split()))
    # 결정은 "### D12." 제목과 초기 표의 "| D1 |" 두 모양이다
    decisions = len(set(re.findall(r"\bD(\d+)\b\s*[.|]", (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8"))))
    ai_rows = sum(1 for line in (ROOT / "docs" / "ai-log.md").read_text(encoding="utf-8").splitlines()
                  if line.startswith("| 2026"))
    # 시험 수는 pytest가 모으는 개수 (여러 경우를 도는 시험은 경우마다 하나)
    collected = subprocess.run(["uv", "run", "pytest", "--collect-only", "-q"], cwd=ROOT, capture_output=True,
                               text=True, encoding="utf-8").stdout
    m = re.search(r"(\d+) tests collected", collected)
    tests = m.group(1) if m else "500+"
    return {"days": str(days), "decisions": str(decisions), "ai_rows": str(ai_rows), "tests": tests}


# ── 그리기 도구 ──

def _fonts(run, mono: bool = False) -> None:
    """라틴·동아시아 글꼴을 함께 정한다. 라틴만 정하면 한글은 테마 글꼴로 바뀐다.
    언어를 한국어로 표시해야 PowerPoint가 낱말(띄어쓰기) 단위로 줄을 바꾼다. 없으면 '나간
다'처럼 글자 단위로 끊는다
    (11/12 PowerPoint로 시험: 문단의 eaLnBrk·latinLnBrk는 효과가 없었다)."""
    face = MONO if mono else FONT
    run.font.name = face
    rpr = run._r.get_or_add_rPr()
    rpr.set("lang", "ko-KR")
    rpr.set("altLang", "en-US")
    ea = rpr.find(qn("a:ea"))
    if ea is None:
        ea = rpr.makeelement(qn("a:ea"), {})
        latin = rpr.find(qn("a:latin"))
        latin.addnext(ea)
    ea.set("typeface", FONT)


def text(slide, x, y, w, h, parts, size=T_BODY, color=INK, bold=False, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, mono=False, spacing=None, line=1.15, name=None, hang=None):
    """parts: 문자열, 또는 줄마다 [(글, {옵션}), ...]의 목록. 옵션: size, color, bold, mono.
    line: 줄 간격(배수), spacing: 문단 뒤 간격(pt), hang: 목록의 둘째 줄부터 들여 쓸 폭(인치, '·  ' 뒤에 맞춘다)."""
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        box.name = name
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    lines = [[(parts, {})]] if isinstance(parts, str) else parts
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = line
        if spacing:
            p.space_after = Pt(spacing)
        if hang:
            ppr = p._p.get_or_add_pPr()
            ppr.set("marL", str(Inches(hang)))
            ppr.set("indent", str(-Inches(hang)))
        for s, o in ([(ln, {})] if isinstance(ln, str) else ln):
            r = p.add_run()
            r.text = s
            r.font.size = Pt(o.get("size", size))
            r.font.bold = o.get("bold", bold)
            r.font.color.rgb = rgb(o.get("color", color))
            _fonts(r, o.get("mono", mono))
    return box


def rect(slide, x, y, w, h, fill, line=None, radius=None, name=None, line_w=1.0):
    """면. fill이 None이면 속이 빈 테두리만. radius가 없으면 각진 사각형. 그림자는 없앤다."""
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    shp = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    if radius:
        shp.adjustments[0] = radius
    if fill:
        shp.fill.solid()
        shp.fill.fore_color.rgb = rgb(fill)
    else:
        shp.fill.background()
    if line:
        shp.line.color.rgb = rgb(line)
        shp.line.width = Pt(line_w)
    else:
        shp.line.fill.background()
    shp.shadow.inherit = False
    if name:
        shp.name = name
    return shp


def rule(slide, x1, y1, x2, y2, color=INK, width=1.0, arrow=False):
    """선. arrow면 끝에 화살촉."""
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = rgb(color)
    c.line.width = Pt(width)
    if arrow:
        ln = c.line._get_or_add_ln()
        ln.append(ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"}))
    return c


def card(slide, x, y, w, h, title, band=BAND, pad=PAD, band_h=BAND_H, name=None):
    """색 머리 띠 상자 (#126): 위에 진한 색 띠 + 흰 굵은 제목(16pt), 아래는 흰 바탕, 테두리는 띠와 같은 색 1.5pt.
    본문을 쓸 자리 (x, y, 폭)을 돌려준다."""
    label = name or title
    rect(slide, x, y, w, h, WHITE, line=band, line_w=1.5, name=label)
    rect(slide, x, y, w, band_h, band, name=f"{label} 띠")
    text(slide, x + pad, y, w - 2 * pad, band_h, title, size=T_BODY, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
    return x + pad, y + band_h + 0.15, w - 2 * pad


def label_row(slide, x, y, w, h, label, label_w, fill=BAND):
    """이름표 칸을 채운 한 줄 상자 [이름표 | 내용] (#126). 내용이 시작하는 x를 돌려준다."""
    rect(slide, x, y, w, h, WHITE, line=fill, line_w=1.5, name=label)
    rect(slide, x, y, label_w, h, fill, name=f"{label} 칸")
    text(slide, x + PAD, y, label_w - 2 * PAD, h, label, size=T_BODY, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
    return x + label_w + 0.25


def chip(slide, x, y, label, size=T_BODY, h=0.4):
    """가명 토큰 칩 [고객1] — 이 발표의 반복 모양."""
    w = 0.18 + len(label) * size * 0.0105
    b = rect(slide, x, y, w, h, YELLOW, radius=0.18, name=f"토큰 {label}")
    tf = b.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = label
    r.font.size = Pt(size)
    r.font.bold = True
    r.font.color.rgb = rgb(INK)
    _fonts(r, mono=True)
    return x + w


def header(slide, kicker, title_):
    """모든 본문 장의 머리: 작은 머리말(파랑) + 제목."""
    text(slide, L, 0.45, W, 0.3, kicker, size=T_SMALL, bold=True, color=BLUE)
    text(slide, L, 0.8, W, 0.7, title_, size=T_TITLE, bold=True, name="제목")


def footer(slide, n):
    """쪽 번호만 둔다(#126: 꼬리말 글은 뺐다)."""
    text(slide, 12.58 - 0.6, 6.95, 0.6, 0.3, str(n), size=T_SMALL, align=PP_ALIGN.RIGHT)


def dark(slide):
    """표지·마무리: 진한 남색 바탕 (#126)."""
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = rgb(NAVY)


def columns(n, gap=0.45, x0=L, width=W):
    """본문 폭을 n칸으로: (x, 칸 폭) 목록."""
    w = (width - gap * (n - 1)) / n
    return [(x0 + i * (w + gap), w) for i in range(n)]


def check_text(prs) -> None:
    """모든 글씨의 크기와 색이 정한 것(SIZES, TEXT_COLORS) 중 하나인지 본다 (#124, #126).
    고치다 13pt나 회색 글씨가 다시 들어오면 만들지 않는다."""
    bad = []
    for n, slide in enumerate(prs.slides, 1):
        for shp in slide.shapes:
            if not shp.has_text_frame:
                continue
            for p in shp.text_frame.paragraphs:
                for r in p.runs:
                    if not r.text.strip():
                        continue
                    size = r.font.size.pt if r.font.size else None
                    color = str(r.font.color.rgb) if r.font.color.type is not None else None
                    if size not in SIZES:
                        bad.append(f"{n}장 '{r.text.strip()[:15]}' 크기 {size}pt")
                    if color not in TEXT_COLORS:
                        bad.append(f"{n}장 '{r.text.strip()[:15]}' 색 {color}")
    if bad:
        raise SystemExit("정한 크기(" + ", ".join(map(str, sorted(SIZES))) + "pt)·글씨 색이 아닌 글씨:\n" + "\n".join(bad))


def notes(slide, s):
    slide.notes_slide.notes_text_frame.text = s


def talk_notes() -> dict[int, str]:
    """발표 대본(talk-script.md)에서 장마다 노트를 읽는다 (#120). '## 번호. 제목 — 시간' 아래의 '> ' 줄이 그 장의 말이다.
    노트 = '(시간)' + 말한 줄을 줄마다. 대본을 한곳에만 두려고 노트를 여기에 따로 적지 않는다."""
    found: dict[int, list[str]] = {}
    n = None
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        if m := re.match(r"## (\d+)\. .+? — (.+)$", line):
            n = int(m.group(1))
            found[n] = [f"({m.group(2).strip()})"]
        elif n and line.startswith("> "):
            found[n].append(line[2:].strip())
    return {k: "\n".join(v) for k, v in found.items()}


def new_slide(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


# ── 장 ──

def s1_cover(prs):
    s = new_slide(prs)
    dark(s)
    text(s, 0.9, 1.0, 11, 0.35, "인공지능프로젝트 결과물 발표  ·  2026. 11. 17", size=T_SMALL, color=LIGHT)
    text(s, 0.9, 1.45, 11.5, 1.1, [[("가림게이트", {"size": T_COVER, "bold": True}),
                                   ("   GarimGate", {"size": T_TITLE, "color": LIGHT})]], color=WHITE)
    text(s, 0.9, 2.6, 11.5, 0.5, "API 주소 한 줄로 붙이는 개인정보 보호 · 토큰 절약 게이트웨이", size=T_HEAD, color=WHITE)
    # 반복 모양: 원문 → 가명 토큰. 이름표 칸을 채운 두 줄
    x0, w, lw, h = 0.9, 11.53, 2.4, 0.8
    y1, y2 = 3.7, 4.65
    bx = label_row(s, x0, y1, w, h, "내가 쓴 요청", lw, fill=BLUE)
    text(s, bx, y1, x0 + w - bx - PAD, h, "김민수 고객(010-1234-5678)에게 배송 지연 사과 메일 써줘", size=T_HEAD,
         anchor=MSO_ANCHOR.MIDDLE)
    bx = label_row(s, x0, y2, w, h, "외부 AI가 보는 글", lw, fill=BLUE)
    cy = y2 + (h - 0.46) / 2
    x = chip(s, bx, cy, "[고객1]", size=T_HEAD, h=0.46)
    text(s, x + 0.1, y2, 0.8, h, "고객(", size=T_HEAD, anchor=MSO_ANCHOR.MIDDLE)
    x = chip(s, x + 0.82, cy, "[전화번호1]", size=T_HEAD, h=0.46)
    text(s, x + 0.08, y2, 5.5, h, ")에게 배송 지연 사과 메일 써줘", size=T_HEAD, anchor=MSO_ANCHOR.MIDDLE)
    text(s, 0.9, 6.3, 8, 0.4, PRESENTER, size=T_BODY, color=WHITE)


def s2_problem(prs):
    s = new_slide(prs)
    header(s, "문제", "AI를 업무에 붙이면 생기는 세 가지 문제")
    items = [
        ("01", "개인정보가 그대로 나간다", "고객 이름·전화번호·주민번호가 요청에 담긴 채 외부 AI 서버로 전송된다", RED),
        ("02", "쉬운 질문도 비싼 모델로", "“배송 언제 와요?”까지 고성능 모델로 보내 비용과 지연이 커진다", AMBER),
        ("03", "막는 도구는 무겁다", "무거운 로컬 LLM과 복잡한 설정이 필요해 중소기업이 도입하기 어렵다", INK),
    ]
    for (n, head, body, accent), (x, w) in zip(items, columns(3)):
        bx, by, bw = card(s, x, TOP + 0.1, w, 3.05, head)
        text(s, bx, by, bw, 0.7, n, size=T_STAT, bold=True, color=accent)
        text(s, bx, by + 0.8, bw, 1.5, body, size=T_BODY, line=1.3)
    y, h = 5.4, 0.7
    bx = label_row(s, L, y, W, h, "대상", 1.3)
    text(s, bx, y, L + W - bx - PAD, h,
         "AI 챗봇·AI 기능을 운영하는 중소기업·스타트업 개발팀, 사내 AI 사용을 관리하는 보안 담당자", anchor=MSO_ANCHOR.MIDDLE)
    footer(s, 2)


def s3_solution(prs):
    s = new_slide(prs)
    header(s, "해결", "API 주소만 바꾸면, 가리고 · 판단하고 · 되돌린다")
    nodes = [  # (제목, 설명, 폭, 띠 색) — 폭은 제목 16pt·설명 14pt가 한 줄에 들어가게. 띠 색은 어디서 도는지 (#126)
        ("기존 서비스", "보내는 곳만\n가림게이트로", 1.5, DARK),    # OpenAI로 보내던 주소 한 줄만 바꾼다 (#122)
        ("① 가리기", "이름·번호·주소\n→ [고객1]", 1.55, GREEN),
        ("② 로컬 판단", "기밀인가\n(원문을 본다)", 1.55, GREEN),
        ("③ 클라우드 판단", "원본 필요·위험\n·난이도", 2.0, BAND),
        ("④ 경로 → LLM", "정해 둔 답·저렴\n·고성능·막음", 1.95, BAND),
        ("⑤ 복원", "[고객1] → 김민수", 1.8, GREEN),
    ]
    gap = (W - sum(n[2] for n in nodes)) / (len(nodes) - 1)
    y, h = 2.5, 1.55
    xs = []
    x = L
    for head, desc, w, band in nodes:
        bx, by, bw = card(s, x, y, w, h, head, band=band, pad=0.12)
        text(s, bx, by, bw, 0.8, desc, size=T_SMALL, line=1.15)
        xs.append((x, w))
        x += w + gap
    for (x1, w1), (x2, _) in zip(xs, xs[1:]):
        rule(s, x1 + w1 + 0.03, y + h / 2, x2 - 0.03, y + h / 2, width=1.5, arrow=True)
    # 영역 표시: 상자 위에 이름 + 굵은 색 선 (띠 색과 같다)
    zones = [(1, 2, "이 PC 안 — 원본은 여기서만", GREEN), (3, 4, "밖 — 가명화된 문장만", BLUE), (5, 5, "이 PC 안", GREEN)]
    for a, b, label, color in zones:
        x1, x2 = xs[a][0], xs[b][0] + xs[b][1]
        text(s, x1, 1.95, x2 - x1, 0.3, label, size=T_SMALL, bold=True, color=color)
        rule(s, x1, 2.32, x2, 2.32, color=color, width=2.5)
    strengths = [("보안", "원본은 이 PC 밖으로 안 나간다. 외부에는 가명 토큰만", GREEN),
                 ("토큰 절약", "쉬운 질문은 정해 둔 답이나 저렴한 모델로", BAND),
                 ("간편함", "로컬 LLM 없이 CPU만, 설치 스크립트 하나, 설정은 화면에서", BAND)]
    for (head, body, band), (x, w) in zip(strengths, columns(3)):
        bx, by, bw = card(s, x, 4.55, w, 1.75, head, band=band)
        text(s, bx, by, bw, 1.0, body, size=T_BODY, line=1.3)
    footer(s, 3)


def s4_ai_roles(prs):
    s = new_slide(prs)
    header(s, "생성형 AI의 역할", "기능마다 생성형 AI가 하는 일")
    rows = [
        ("가명화", "이름 인식 모델이 사전에 없는 이름·주소의 위치를 찾는다 (로컬, 14.7MB)", "정규식·사전으로 찾아 [고객1]로 바꾸고, 답변에서 되돌린다"),
        ("기밀 판단", "판단 모델이 원문을 보고 ‘기밀인가’의 확률을 낸다 (로컬, CPU)", "기준값과 비교해 엄격 수준에서 막는다"),
        ("라우팅", "판단 API가 가명화된 문장으로 원본 필요·위험·난이도·의도를 판단한다", "판단 결과로 정해 둔 답·저렴·고성능·막음을 고른다"),
        ("답변", "LLM이 가명 토큰이 든 요청으로 답을 쓴다", "토큰을 원래 이름·번호로 되돌린다 (조사까지 맞춤)"),
        ("FAQ", "쓰지 않는다", "등록된 질문은 정해 둔 답으로 바로 (비용 0)"),
    ]
    cx = [(L, 1.6), (L + 1.6, 5.6), (L + 7.2, W - 7.2)]          # 칸 (x, 폭): 기능 · AI가 하는 일 · 코드가 하는 일
    head_h, row_h = 0.5, 0.72
    body_top = TOP + head_h
    body_h = row_h * len(rows)
    rect(s, cx[1][0], body_top, cx[1][1], body_h, TINT, name="AI 열")             # AI 열만 연파랑 바탕
    rect(s, L, TOP, W, head_h, BAND, name="표 머리")
    for (x, w), label in zip(cx, ("기능", "AI가 하는 일", "코드가 하는 일")):
        text(s, x + PAD, TOP, w - 2 * PAD, head_h, label, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE)
    y = body_top
    for i, (feat, ai, code) in enumerate(rows):
        for (x, w), val, bold in zip(cx, (feat, ai, code), (True, False, False)):
            text(s, x + PAD, y, w - 2 * PAD, row_h, val, bold=bold, line=1.1, anchor=MSO_ANCHOR.MIDDLE)
        y += row_h
        if i < len(rows) - 1:
            rule(s, L, y, L + W, y, color=BAND, width=0.75)
    for x, _ in cx[1:]:
        rule(s, x, body_top, x, body_top + body_h, color=BAND, width=0.75)
    rect(s, L, TOP, W, head_h + body_h, None, line=BAND, line_w=1.5, name="표 테두리")
    text(s, L, y + 0.25, W, 0.45, [[("판단은 판단 모델", {"color": BLUE, "bold": True}), ("     ·     ", {}),
                                     ("생성은 LLM", {"color": AMBER, "bold": True}), ("     ·     ", {}),
                                     ("가리기·복원·계산은 코드", {"bold": True})]], size=T_HEAD, align=PP_ALIGN.CENTER)
    footer(s, 4)


def s5_demo(prs):
    s = new_slide(prs)
    header(s, "시연", "30초 시연")
    pic = s.shapes.add_picture(str(DEMO_SHOT), Inches(L), Inches(TOP), width=Inches(7.35))
    pic.name = "시연 화면 (녹화 영상으로 바꾸기)"            # add_video.ps1이 이 이름을 찾아 영상으로 바꾼다
    pic.line.color.rgb = rgb(BAND)
    pic.line.width = Pt(1.5)
    x0, w = 8.4, 12.58 - 8.4
    steps = [  # (띠 제목, 요청, 결과)
        ("1  배송 문의", "“주문한 거 배송 언제 와요?”", "LLM 없이 정해 둔 답 · 비용 0"),
        ("2  개인정보가 든 요청", "“김민수 고객(010-…)에게 사과 메일”", None),
        ("3  실제 값이 필요한 요청", "“이 주민번호로 나이 계산해줘”", "밖으로 안 보내고 안내"),
        ("4  비용 카드", "기존 방식(전부 고성능) vs 게이트웨이", "화면 위 카드에 차이가 쌓인다"),
    ]
    h, gap, bh = 1.155, 0.1, 0.42                   # 네 상자 합이 영상 높이(4.92")와 같다
    y = TOP
    for head, ask, result in steps:
        bx, by, bw = card(s, x0, y, w, h, head, band_h=bh, pad=0.12)
        by = y + bh + 0.1
        text(s, bx, by, bw, 0.3, ask)
        if result is None:
            text(s, bx, by + 0.34, 0.85, 0.3, "밖으로는", size=T_SMALL)
            xx = chip(s, bx + 0.8, by + 0.32, "[고객1]", size=T_SMALL, h=0.3)
            text(s, xx + 0.1, by + 0.34, 2.2, 0.3, "답변엔 김민수", size=T_SMALL)
        else:
            text(s, bx, by + 0.34, bw, 0.3, result, size=T_SMALL)
        y += h + gap
    footer(s, 5)


def s6_results(prs):
    s = new_slide(prs)
    header(s, "결과", "측정 결과")
    text(s, L, 1.5, W, 0.3, "테스트셋 190행(업종 4개) + 생성 데이터 622개 · 이름·번호는 모두 가상 값 · 2026-11-08 측정",
         size=T_SMALL)
    stats = [  # 띠 제목은 16pt로 한 줄에 들어가게 줄였다(#126)
        ("99.5%", "개인정보 가명화", "619 / 622\n전화·이메일·주민·카드·\n계좌·주소 100%", GREEN),   # 띄어쓰기가 없어 직접 끊는다
        ("100%", "실제 값 필요 요청 막기", "18 / 18\n잘못 막음 0.6%", RED),
        ("89%", "경로 정확도", "190행 중 169\n쇼핑몰(시연 업종) 97%", BLUE),
        ("95~100%", "쉬운 요청 비용 절감", "정해 둔 답 100%\n저렴한 모델 95%", AMBER),
    ]
    for (big, label, detail, color), (x, w) in zip(stats, columns(4, gap=0.25)):
        bx, by, bw = card(s, x, 1.95, w, 2.35, label, pad=0.12)
        text(s, bx, by - 0.05, bw, 0.75, big, size=T_STAT, bold=True, color=color)
        text(s, bx, by + 0.75, bw, 0.8, detail, size=T_SMALL, line=1.2)
    facts = [("판단 지연", "평균 0.50초 · p95 0.65초"),
             ("이 PC의 모델", "판단 모델(CPU) + 이름 인식 14.7MB, GPU 없음"),
             ("전체 비용 절감", "요청 구성에 달림 — 복잡한 요청 10%면 48%, 이 테스트셋은 10%"),
             # 16pt로 한 줄에 들어가게 줄였다(#124). 뜻은 같다: 충족·정확성 차이 ±0.05 (qa-prep.md)
             ("답변 품질 (38건)", "가려서 보내도 요청 충족·정확성은 그대로(5점 중 4.8·4.6), 자연스러움 −0.3 → 지시문 보완")]
    y, h = 4.55, 0.42
    for k, v in facts:
        bx = label_row(s, L, y, W, h, k, 2.1)
        text(s, bx, y, L + W - bx - PAD, h, v, anchor=MSO_ANCHOR.MIDDLE)
        y += h + 0.08
    footer(s, 6)


def s7_ai_dev(prs, nums):
    s = new_slide(prs)
    header(s, "개발 과정", "개발에 AI 활용 — AI에게 맡긴 것, 내가 결정한 것")
    left = ["코드와 시험 작성 — 기능마다 시험을 함께, 고장 내서 시험이 잡는지 확인",
            "측정 실행기와 결과 분석 — 테스트셋 190행, 가명화·경로·비용",
            "새 도구는 공식 문서부터 확인 — Ollaya, OpenAI, Cloudflare",
            "커밋마다 자동 보안 검토 — 지적을 받아 고침"]
    right = ["판단을 둘로 나누기 — 원문이 필요한 판단만 이 PC, 나머지는 가명화 문장으로 클라우드",
             "무엇을 막고 무엇을 보낼지 — 보안 수준, 기준값, 주소 처리",
             "화면 설계 — 써 보고 세 번 고침",
             "범위 줄이기 — 파인튜닝 보류, 설문은 웹 체험판으로"]
    for (head, items, band), (x, w) in zip([("AI(Claude Code)에게 맡긴 일", left, BAND), ("내가 판단하고 결정한 일", right, DARK)],
                                           columns(2)):
        bx, by, bw = card(s, x, TOP, w, 3.45, head, band=band)
        text(s, bx, by + 0.05, bw, 2.7, [[("·  ", {"bold": True}), (t, {})] for t in items], spacing=8, line=1.2,
             hang=HANG)
    strip = [(nums["days"], "커밋한 날"), (nums["decisions"], "설계 결정 기록"), (nums["ai_rows"], "AI 활용 기록"),
             (nums["tests"], "자동 시험")]
    for (v, k), (x, w) in zip(strip, columns(4, gap=0.25)):
        bx, by, bw = card(s, x, 5.5, w, 1.3, k, band_h=0.42)
        text(s, bx, by - 0.08, bw, 0.65, v, size=T_STAT, bold=True, color=BLUE)
    footer(s, 7)


def s8_limits(prs):
    s = new_slide(prs)
    header(s, "돌아보기", "한계와 다음 단계")
    (lx, lw), (rx, rw) = columns(2)
    bx, by, bw = card(s, lx, TOP, lw, 4.6, "한계", band=RED)
    text(s, bx, by, bw, 0.85, [[("33%", {"size": T_STAT, "bold": True, "color": RED}),
                                ("   개인정보가 든 요청 중 막힌 비율", {"bold": True})]])
    text(s, bx, by + 0.95, bw, 1.4, "테스트셋 19 / 57. 답이 실제 값에 달린 요청(주민번호로 나이 계산, 주소 확인)은 가린 글로 풀 수 없어 "
                                    "밖으로 보내지 않고 막는다. 안전하지만 사용자는 그 일을 못 한다", line=1.25)
    text(s, bx, by + 2.55, bw, 1.0, [[("·  ", {"bold": True, "color": RED}), ("이름 인식 99% — 붙여 쓴 이름은 놓칠 수 있다", {})],
                                      [("·  ", {"bold": True, "color": RED}), ("전체 비용 절감은 요청 구성에 달린다", {})]],
         spacing=6, line=1.2, hang=HANG)
    bx, by, bw = card(s, rx, TOP, rw, 4.6, "다음 단계")
    nexts = [("완전 로컬 모드", "막힌 요청은 밖으로 보내지 않고 이 PC의 작은 LLM이 처리"),
             ("계산은 코드로", "나이·날짜 계산은 원본을 PC 안에서 코드가 풀고 결과만 LLM에"),
             ("배포·사용자 평가", "웹 체험판 공개 중(11/14~) → 사용자 설문 10명 이상, 12/1까지")]
    y = by
    for i, (head, body) in enumerate(nexts, 1):
        text(s, bx, y - 0.04, 0.4, 0.45, str(i), size=T_HEAD, bold=True, color=BLUE)
        text(s, bx + 0.4, y, bw - 0.4, 0.4, head, bold=True)
        text(s, bx + 0.4, y + 0.42, bw - 0.4, 0.7, body, line=1.2)
        y += 1.3
    footer(s, 8)


def survey_qr() -> io.BytesIO:
    """설문 폼 QR(PNG). 오류 정정 M: 화면 반사·먼 자리에서도 읽히게. 둘레 4칸은 QR 규격의 여백."""
    import segno
    buf = io.BytesIO()
    segno.make(SURVEY_URL, error="m", micro=False).save(buf, kind="png", scale=12, border=4, dark="#111827")
    buf.seek(0)
    return buf


def s9_end(prs):
    s = new_slide(prs)
    dark(s)
    text(s, 0.9, 1.7, 8.2, 1.0, "감사합니다", size=T_COVER, bold=True, color=WHITE)
    text(s, 0.9, 2.95, 8.2, 0.5, "가림게이트 — 보안을 지키면서 아끼는 관문, API 주소 한 줄로", size=T_HEAD, color=WHITE)
    x = chip(s, 0.9, 3.85, "[고객1]", size=T_HEAD, h=0.46)
    x = chip(s, x + 0.15, 3.85, "[전화번호1]", size=T_HEAD, h=0.46)
    chip(s, x + 0.15, 3.85, "[주소1]", size=T_HEAD, h=0.46)
    # 설문 QR은 흰 상자 안에: 어두운 바탕에 바로 두면 QR 둘레의 흰 여백이 떠 보이고, 상자가 있어야 찍을 곳이 눈에 띈다
    cw, ch, qs = 3.3, 3.45, 2.3                           # 폭은 아래 설명(14pt)이 한 줄에 들어가게
    cx, cy = 12.43 - cw, 1.3
    bx, by, bw = card(s, cx, cy, cw, ch, "써 보고 설문해 주세요", band=BLUE)
    qr = s.shapes.add_picture(survey_qr(), Inches(cx + (cw - qs) / 2), Inches(by - 0.05), width=Inches(qs), height=Inches(qs))
    qr.name = "설문 QR"
    text(s, cx, by + qs, cw, 0.35, "체험 + 설문 약 10분 · 가상 정보만", size=T_SMALL, align=PP_ALIGN.CENTER)
    text(s, 0.9, 5.6, 6, 0.4, "github.com/baesanggue/ai-gateway", color=LIGHT)
    text(s, 6.9, 5.6, 5.53, 0.4, PRESENTER, color=WHITE, align=PP_ALIGN.RIGHT)


def main() -> None:
    nums = repo_numbers()
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    prs.core_properties.title = "가림게이트 결과물 발표"
    prs.core_properties.author = "배상규"
    s1_cover(prs)
    s2_problem(prs)
    s3_solution(prs)
    s4_ai_roles(prs)
    s5_demo(prs)
    s6_results(prs)
    s7_ai_dev(prs, nums)
    s8_limits(prs)
    s9_end(prs)
    check_text(prs)
    script = talk_notes()
    if set(script) != set(range(1, len(prs.slides) + 1)) or any(v.count("\n") < 1 for v in script.values()):
        raise SystemExit(f"talk-script.md의 장({sorted(script)})이 슬라이드 {len(prs.slides)}장과 맞지 않거나 말한 줄이 없다")
    for i, slide in enumerate(prs.slides, 1):
        notes(slide, script[i])
    prs.save(OUT)
    print(f"saved {OUT} ({nums})")


if __name__ == "__main__":
    main()
