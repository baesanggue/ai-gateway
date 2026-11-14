"""11/17 결과 발표 자료를 만든다 (#95). 5분, 9장: 개요 → AI 활용 내역 → 결과.

    python docs/presentation/build_deck.py      # 시스템 파이썬(python-pptx 1.0, segno 1.6)으로 실행

- 숫자는 만들 때 저장소에서 읽는다(커밋한 날, 설계 결정, AI 활용 기록, 시험 수). 발표 전날 다시 만들면 최신이 된다
- 측정 숫자는 docs/m3-numbers.md, 한계는 docs/limitations.md에서 왔다
- 시연 장에는 시연 화면 캡처를 넣어 둔다. 다시 만든 뒤에는 add_video.ps1로 30초 영상(demo-30s.mp4, 자동 재생)으로 바꾼다 (#106)
- 디자인 (11/13 다시, #111: "AI로 만든 느낌 말고 자연스럽게, 간격·배치 정리, 살짝 밝게")
  - 모든 장이 밝은 바탕. 둥근 카드·번호 동그라미 대신 여백과 가는 선으로 나눈다
  - 같은 격자: 좌우 여백 0.75", 위에 작은 머리말 + 제목, 본문은 1.85"부터, 아래에 작은 쪽 표시
  - 색은 파랑 하나를 기본으로, 막힘·한계만 빨강. 가명 토큰 칩([고객1], 노랑)은 제품의 상징이라 남긴다
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
INK, BODY, SUB, MUTED = "1F2937", "374151", "6B7280", "9CA3AF"
LINE, RULE, SOFT, PANEL = "E5E7EB", "CBD5E1", "F8FAFC", "F1F5F9"
BLUE, BLUE_INK, BLUE_SOFT = "2563EB", "1E40AF", "EFF4FF"
GREEN, RED, AMBER = "0F7B5A", "C0392B", "B45309"
YELLOW, WHITE = "FDE68A", "FFFFFF"

L, W = 0.75, 11.83            # 좌우 여백 0.75" → 본문 폭 (슬라이드 13.333")
TOP = 1.85                    # 본문이 시작하는 높이


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


def text(slide, x, y, w, h, parts, size=16, color=INK, bold=False, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, mono=False, spacing=None, line=1.15, name=None):
    """parts: 문자열, 또는 줄마다 [(글, {옵션}), ...]의 목록. 옵션: size, color, bold, mono.
    line: 줄 간격(배수), spacing: 문단 뒤 간격(pt)."""
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
        for s, o in ([(ln, {})] if isinstance(ln, str) else ln):
            r = p.add_run()
            r.text = s
            r.font.size = Pt(o.get("size", size))
            r.font.bold = o.get("bold", bold)
            r.font.color.rgb = rgb(o.get("color", color))
            _fonts(r, o.get("mono", mono))
    return box


def rect(slide, x, y, w, h, fill, line=None, radius=None, name=None):
    """면. radius가 없으면 각진 사각형. 그림자는 없앤다."""
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    shp = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    if radius:
        shp.adjustments[0] = radius
    shp.fill.solid()
    shp.fill.fore_color.rgb = rgb(fill)
    if line:
        shp.line.color.rgb = rgb(line)
        shp.line.width = Pt(1)
    else:
        shp.line.fill.background()
    shp.shadow.inherit = False
    if name:
        shp.name = name
    return shp


def rule(slide, x1, y1, x2, y2, color=LINE, width=1.0, arrow=False):
    """가는 선. arrow면 끝에 작은 화살촉."""
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = rgb(color)
    c.line.width = Pt(width)
    if arrow:
        ln = c.line._get_or_add_ln()
        ln.append(ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"}))
    return c


def chip(slide, x, y, label, size=15, h=0.4):
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
    text(slide, L, 0.5, W, 0.3, kicker, size=12, bold=True, color=BLUE)
    text(slide, L, 0.82, W, 0.7, title_, size=28, bold=True, name="제목")


def footer(slide, n):
    text(slide, L, 6.98, 6, 0.3, "가림게이트 · 인공지능프로젝트 결과물 발표", size=10, color=MUTED)
    text(slide, 12.58 - 0.6, 6.98, 0.6, 0.3, str(n), size=10, color=MUTED, align=PP_ALIGN.RIGHT)


def columns(n, gap=0.6, x0=L, width=W):
    """본문 폭을 n칸으로: (x, 칸 폭) 목록."""
    w = (width - gap * (n - 1)) / n
    return [(x0 + i * (w + gap), w) for i in range(n)]


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
    text(s, 0.9, 1.05, 11, 0.35, "인공지능프로젝트 결과물 발표  ·  2026. 11. 17", size=13, color=SUB)
    text(s, 0.9, 1.5, 11, 1.1, [[("가림게이트", {"size": 56, "bold": True}),
                                  ("   GarimGate", {"size": 24, "color": BLUE})]])
    text(s, 0.9, 2.65, 11.5, 0.5, "API 주소 한 줄로 붙이는 개인정보 보호 · 토큰 절약 게이트웨이", size=22, color=BODY)
    # 반복 모양: 원문 → 가명 토큰
    rect(s, 0.9, 3.75, 11.53, 1.95, PANEL, radius=0.04, name="원문과 나가는 글")
    text(s, 1.3, 4.07, 2.0, 0.35, "내가 쓴 요청", size=12, color=SUB)
    text(s, 3.2, 4.0, 9.0, 0.45, "김민수 고객(010-1234-5678)에게 배송 지연 사과 메일 써줘", size=18)
    rule(s, 1.3, 4.72, 12.03, 4.72, color=RULE, width=0.75)
    text(s, 1.3, 5.04, 2.0, 0.35, "외부 AI가 보는 글", size=12, color=SUB)
    x = chip(s, 3.2, 4.95, "[고객1]", size=16)
    text(s, x + 0.1, 4.99, 0.75, 0.4, "고객(", size=18)
    x = chip(s, x + 0.78, 4.95, "[전화번호1]", size=16)
    text(s, x + 0.08, 4.99, 5.0, 0.4, ")에게 배송 지연 사과 메일 써줘", size=18)
    text(s, 0.9, 6.35, 8, 0.4, PRESENTER, size=15, color=BODY)


def s2_problem(prs):
    s = new_slide(prs)
    header(s, "문제", "AI를 업무에 붙이면 생기는 세 가지 문제")
    items = [
        ("01", "개인정보가 그대로 나간다", "고객 이름·전화번호·주민번호가 요청에 담긴 채 외부 AI 서버로 전송된다", RED),
        ("02", "쉬운 질문도 비싼 모델로", "“배송 언제 와요?”까지 고성능 모델로 보내 비용과 지연이 커진다", AMBER),
        ("03", "막는 도구는 무겁다", "무거운 로컬 LLM과 복잡한 설정이 필요해 중소기업이 도입하기 어렵다", SUB),
    ]
    cols = columns(3, gap=0.7)
    for i, ((n, head, body, accent), (x, w)) in enumerate(zip(items, cols)):
        text(s, x, 2.3, w, 0.6, n, size=30, bold=True, color=accent)
        text(s, x, 3.1, w, 0.5, head, size=20, bold=True)
        text(s, x, 3.75, w, 1.5, body, size=17, color=BODY, line=1.3)
        if i:
            rule(s, x - 0.35, 2.4, x - 0.35, 5.0)
    rule(s, L, 5.5, L + W, 5.5)
    text(s, L, 5.8, 0.9, 0.4, "대상", size=14, bold=True, color=BLUE)
    text(s, L + 0.9, 5.78, W - 0.9, 0.5,
         "AI 챗봇·AI 기능을 운영하는 중소기업·스타트업 개발팀, 사내 AI 사용을 관리하는 보안 담당자", size=16, color=BODY)
    footer(s, 2)


def s3_solution(prs):
    s = new_slide(prs)
    header(s, "해결", "API 주소만 바꾸면, 가리고 · 판단하고 · 되돌린다")
    nodes = [  # (제목, 설명, 폭)
        ("기존 서비스", "보내는 곳만\n가림게이트로", 1.55),  # OpenAI로 보내던 주소 한 줄만 바꾼다 (#122)
        ("① 가리기", "이름·번호·주소\n→ [고객1]", 1.75),
        ("② 로컬 판단", "기밀인가\n(원문을 본다)", 1.7),
        ("③ 클라우드 판단", "원본 필요·위험\n·난이도", 1.95),
        ("④ 경로 → LLM", "정해 둔 답·저렴\n·고성능·막음", 1.95),
        ("⑤ 복원", "[고객1] → 김민수", 1.75),
    ]
    gap = (W - sum(w for *_, w in nodes)) / (len(nodes) - 1)
    y, h = 2.55, 1.4
    xs = []
    x = L
    for head, desc, w in nodes:
        rect(s, x, y, w, h, WHITE, line=RULE, radius=0.05, name=head)
        text(s, x + 0.15, y + 0.2, w - 0.3, 0.4, head, size=15, bold=True)
        text(s, x + 0.15, y + 0.64, w - 0.3, 0.7, desc, size=13, color=SUB, line=1.15)
        xs.append((x, w))
        x += w + gap
    for (x1, w1), (x2, _) in zip(xs, xs[1:]):
        rule(s, x1 + w1 + 0.04, y + h / 2, x2 - 0.04, y + h / 2, color=MUTED, width=1.25, arrow=True)
    # 영역 표시: 노드 위에 이름 + 색 선
    zones = [(1, 2, "이 PC 안 — 원본은 여기서만", GREEN), (3, 4, "밖 — 가명화된 문장만", BLUE), (5, 5, "이 PC 안", GREEN)]
    for a, b, label, color in zones:
        x1, x2 = xs[a][0], xs[b][0] + xs[b][1]
        text(s, x1, 2.0, x2 - x1, 0.3, label, size=12, bold=True, color=color)
        rule(s, x1, 2.37, x2, 2.37, color=color, width=2)
    strengths = [("보안", "원본은 이 PC 밖으로 안 나간다. 외부에는 가명 토큰만", GREEN),
                 ("토큰 절약", "쉬운 질문은 정해 둔 답이나 저렴한 모델로", BLUE),
                 ("간편함", "로컬 LLM 없이 CPU만, 설치 스크립트 하나, 설정은 화면에서", BODY)]
    rule(s, L, 4.55, L + W, 4.55)
    for i, ((head, body, accent), (x, w)) in enumerate(zip(strengths, columns(3, gap=0.7))):
        text(s, x, 4.85, w, 0.45, head, size=18, bold=True, color=accent)
        text(s, x, 5.35, w, 1.0, body, size=15, color=BODY, line=1.3)
        if i:
            rule(s, x - 0.35, 4.9, x - 0.35, 6.2)
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
    cx = [(L, 1.55), (L + 1.75, 5.55), (L + 7.5, W - 7.5)]          # 기능 · AI가 하는 일 · 코드가 하는 일
    rect(s, cx[1][0] - 0.15, TOP, cx[1][1] + 0.3, 0.55 + 0.74 * len(rows), BLUE_SOFT, name="AI 열")
    for (x, w), label, color in zip(cx, ("기능", "AI가 하는 일", "코드가 하는 일"), (SUB, BLUE_INK, SUB)):
        text(s, x, TOP + 0.13, w, 0.3, label, size=13, bold=True, color=color)
    rule(s, L, TOP + 0.55, L + W, TOP + 0.55, color=RULE, width=1.25)
    y = TOP + 0.55
    for i, (feat, ai, code) in enumerate(rows):
        for (x, w), val, bold, color in zip(cx, (feat, ai, code), (True, False, False), (INK, INK, BODY)):
            text(s, x, y + 0.13, w, 0.6, val, size=14, bold=bold, color=color, line=1.2)
        y += 0.74
        if i < len(rows) - 1:
            rule(s, L, y, L + W, y)
    rule(s, L, y, L + W, y, color=RULE, width=1.25)
    text(s, L, y + 0.4, W, 0.45, [[("판단은 판단 모델", {"color": BLUE, "bold": True}), ("     ·     ", {"color": MUTED}),
                                    ("생성은 LLM", {"color": AMBER, "bold": True}), ("     ·     ", {"color": MUTED}),
                                    ("가리기·복원·계산은 코드", {"bold": True})]], size=18, align=PP_ALIGN.CENTER)
    footer(s, 4)


def s5_demo(prs):
    s = new_slide(prs)
    header(s, "시연", "30초 시연")
    pic = s.shapes.add_picture(str(DEMO_SHOT), Inches(L), Inches(TOP), width=Inches(7.35))
    pic.name = "시연 화면 (녹화 영상으로 바꾸기)"            # add_video.ps1이 이 이름을 찾아 영상으로 바꾼다
    pic.line.color.rgb = rgb(LINE)
    pic.line.width = Pt(1)
    x0, w = 8.65, 12.58 - 8.65
    steps = [
        ("1", "“주문한 거 배송 언제 와요?”", "LLM 없이 정해 둔 답 · 비용 0"),
        ("2", "“김민수 고객(010-…)에게 사과 메일”", None),
        ("3", "“이 주민번호로 나이 계산해줘”", "실제 값이 필요 → 밖으로 안 보내고 안내"),
        ("4", "비용 카드", "기존 방식(전부 고성능) vs 게이트웨이"),
    ]
    y = TOP + 0.05
    for i, (n, head, desc) in enumerate(steps):
        text(s, x0, y, 0.4, 0.45, n, size=20, bold=True, color=BLUE)
        text(s, x0 + 0.45, y + 0.04, w - 0.45, 0.45, head, size=15, bold=True)
        if n == "2":
            text(s, x0 + 0.45, y + 0.52, 0.9, 0.35, "밖으로는", size=13, color=SUB)
            xx = chip(s, x0 + 1.3, y + 0.48, "[고객1]", size=13, h=0.33)
            text(s, xx + 0.1, y + 0.52, 2.2, 0.35, "답변엔 김민수", size=13, color=SUB)
        else:
            text(s, x0 + 0.45, y + 0.52, w - 0.45, 0.6, desc, size=13, color=SUB)
        y += 1.22
        if i < len(steps) - 1:
            rule(s, x0, y - 0.18, 12.58, y - 0.18)
    footer(s, 5)


def s6_results(prs):
    s = new_slide(prs)
    header(s, "결과", "측정 결과")
    text(s, L, 1.55, W, 0.3, "테스트셋 190행(업종 4개) + 생성 데이터 622개 · 이름·번호는 모두 가상 값 · 2026-11-08 측정",
         size=12, color=SUB)
    stats = [
        ("99.5%", "개인정보 가명화", "619 / 622\n전화·이메일·주민·카드·계좌·주소 100%", GREEN),
        ("100%", "실제 값이 필요한 요청 막기", "18 / 18\n잘못 막음 0.6%", RED),
        ("89%", "경로 정확도", "190행 중 169\n쇼핑몰(시연 업종) 97%", BLUE),
        ("95~100%", "쉬운 요청 비용 절감", "정해 둔 답 100%\n저렴한 모델 95%", AMBER),
    ]
    for i, ((big, label, detail, color), (x, w)) in enumerate(zip(stats, columns(4, gap=0.5))):
        text(s, x, 2.2, w, 0.85, big, size=44 if len(big) < 6 else 36, bold=True, color=color)
        text(s, x, 3.15, w, 0.75, label, size=16, bold=True, line=1.1)    # 두 줄이 되어도 아래와 겹치지 않게
        text(s, x, 3.95, w, 0.8, detail, size=13, color=SUB, line=1.25)
        if i:
            rule(s, x - 0.25, 2.3, x - 0.25, 4.65)
    facts = [("판단 지연", "평균 0.50초 · p95 0.65초"),
             ("이 PC에서 도는 모델", "판단 모델(CPU) + 이름 인식 14.7MB, GPU 없음"),
             ("전체 비용 절감", "요청 구성에 달림 — 복잡한 요청 10%면 48%, 이 테스트셋은 10%"),
             ("답변 품질", "가려서 보낸 답의 요청 충족·정확성은 원문과 같음(5점 중 4.8·4.6), 자연스러움 −0.3 → 지시문 보완 (38건)")]
    rule(s, L, 5.0, L + W, 5.0)
    y = 5.2
    for k, v in facts:
        text(s, L, y, 2.3, 0.35, k, size=13, bold=True, color=BLUE_INK)
        text(s, L + 2.3, y, W - 2.3, 0.35, v, size=14, color=BODY)
        y += 0.42
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
    for (head, items, accent), (x, w) in zip([("AI(Claude Code)에게 맡긴 일", left, BLUE), ("내가 판단하고 결정한 일", right, INK)],
                                             columns(2, gap=0.8)):
        text(s, x, TOP + 0.05, w, 0.45, head, size=18, bold=True, color=accent)
        rule(s, x, TOP + 0.6, x + w, TOP + 0.6, color=accent if accent == BLUE else RULE, width=1.5)
        text(s, x, TOP + 0.85, w, 2.8, [[("·  ", {"bold": True, "color": accent}), (t, {})] for t in items],
             size=15, color=BODY, spacing=12, line=1.25)
    rule(s, L, 5.55, L + W, 5.55)
    strip = [(nums["days"], "커밋한 날"), (nums["decisions"], "설계 결정 기록"), (nums["ai_rows"], "AI 활용 기록"),
             (nums["tests"], "자동 시험")]
    for (v, k), (x, w) in zip(strip, columns(4, gap=0.5)):
        text(s, x, 5.75, w, 0.6, v, size=28, bold=True, color=BLUE)
        text(s, x, 6.38, w, 0.35, k, size=12, color=SUB)
    footer(s, 7)


def s8_limits(prs):
    s = new_slide(prs)
    header(s, "돌아보기", "한계와 다음 단계")
    (lx, lw), (rx, rw) = columns(2, gap=0.9)
    text(s, lx, TOP + 0.05, lw, 0.45, "한계", size=18, bold=True, color=RED)
    text(s, lx, TOP + 0.6, lw, 1.0, [[("33%", {"size": 50, "bold": True, "color": RED}),
                                       ("   개인정보가 든 요청 중 막힌 비율", {"size": 15, "bold": True})]])
    text(s, lx, TOP + 1.75, lw, 1.3, "테스트셋 19 / 57. 답이 실제 값에 달린 요청(주민번호로 나이 계산, 주소 확인)은 가린 글로 풀 수 없어 "
                                    "밖으로 보내지 않고 막는다. 안전하지만 사용자는 그 일을 못 한다", size=14, color=BODY, line=1.3)
    rule(s, lx, TOP + 3.15, lx + lw, TOP + 3.15)
    text(s, lx, TOP + 3.35, lw, 1.0, [[("·  ", {"bold": True, "color": RED}), ("이름 인식 99% — 붙여 쓴 이름은 놓칠 수 있다", {})],
                                       [("·  ", {"bold": True, "color": RED}), ("전체 비용 절감은 요청 구성에 달린다", {})]],
         size=14, color=BODY, spacing=6, line=1.25)
    rule(s, rx - 0.45, TOP + 0.1, rx - 0.45, 6.5)
    text(s, rx, TOP + 0.05, rw, 0.45, "다음 단계", size=18, bold=True, color=BLUE)
    nexts = [("완전 로컬 모드", "막힌 요청은 밖으로 보내지 않고 이 PC의 작은 LLM이 처리"),
             ("계산은 코드로", "나이·날짜 계산은 원본을 PC 안에서 코드가 풀고 결과만 LLM에"),
             ("배포·사용자 평가", "웹 체험판 공개 중(11/14~) → 사용자 설문 10명 이상, 12/1까지")]
    y = TOP + 0.75
    for i, (head, body) in enumerate(nexts, 1):
        text(s, rx, y, 0.4, 0.45, str(i), size=20, bold=True, color=BLUE)
        text(s, rx + 0.45, y + 0.04, rw - 0.45, 0.4, head, size=16, bold=True)
        text(s, rx + 0.45, y + 0.5, rw - 0.45, 0.7, body, size=14, color=BODY, line=1.25)
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
    qx, qy, qs = 10.13, 1.75, 2.3                         # 오른쪽 QR: 오른쪽 끝을 아래 가로선 끝(12.43")에 맞춘다
    text(s, 0.9, 1.9, 8.6, 1.0, "감사합니다", size=48, bold=True)
    text(s, 0.9, 3.05, 8.6, 0.5, "가림게이트 — 보안을 지키면서 아끼는 관문, API 주소 한 줄로", size=20, color=BODY)
    x = chip(s, 0.9, 3.95, "[고객1]", size=16)
    x = chip(s, x + 0.15, 3.95, "[전화번호1]", size=16)
    chip(s, x + 0.15, 3.95, "[주소1]", size=16)
    rule(s, qx - 0.5, 1.9, qx - 0.5, 4.85)
    qr = s.shapes.add_picture(survey_qr(), Inches(qx), Inches(qy), width=Inches(qs), height=Inches(qs))
    qr.name = "설문 QR"
    text(s, qx - 0.3, qy + qs + 0.1, qs + 0.6, 0.8,
         [[("직접 써 보고 설문에 답해 주세요", {"size": 13, "bold": True})],
          [("체험 + 설문 약 10분 · 가상 정보만", {"size": 11, "color": SUB})]], align=PP_ALIGN.CENTER, spacing=2)
    rule(s, 0.9, 5.2, 12.43, 5.2)
    text(s, 0.9, 5.45, 6, 0.4, "github.com/baesanggue/ai-gateway", size=15, color=BLUE)
    text(s, 6.9, 5.45, 5.53, 0.4, PRESENTER, size=15, color=BODY, align=PP_ALIGN.RIGHT)


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
    script = talk_notes()
    if set(script) != set(range(1, len(prs.slides) + 1)) or any(v.count("\n") < 1 for v in script.values()):
        raise SystemExit(f"talk-script.md의 장({sorted(script)})이 슬라이드 {len(prs.slides)}장과 맞지 않거나 말한 줄이 없다")
    for i, slide in enumerate(prs.slides, 1):
        notes(slide, script[i])
    prs.save(OUT)
    print(f"saved {OUT} ({nums})")


if __name__ == "__main__":
    main()
