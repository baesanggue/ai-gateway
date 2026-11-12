"""11/17 결과 발표 자료를 만든다 (#95). 5분, 9장: 개요 → AI 활용 내역 → 결과.

    python docs/presentation/build_deck.py      # 시스템 파이썬(python-pptx 1.0)으로 실행

- 숫자는 만들 때 저장소에서 읽는다(커밋한 날, 설계 결정, AI 활용 기록, 시험 수). 발표 전날 다시 만들면 최신이 된다
- 측정 숫자는 docs/m3-numbers.md, 한계는 docs/limitations.md에서 왔다
- 시연 장에는 시연 화면 캡처를 넣어 둔다. 발표 전에 30초 녹화 영상으로 바꾼다(발표자 노트 참고)
- 디자인: 흰 바탕 + 프로그램 화면의 파랑, 표지·마무리는 터미널 색(어두운 남색). 반복하는 모양은 가명 토큰 칩([고객1], 노란색)
"""
import re
import subprocess
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "2026-11-17-garimgate.pptx"
DEMO_SHOT = ROOT / "docs" / "design" / "screen-demo.png"

FONT = "맑은 고딕"
MONO = "Consolas"
NAVY, INK, SUB, LINE = "1E1F24", "111827", "6B7280", "E5E7EB"
BLUE, BLUE_SOFT, BLUE_INK = "2563EB", "EEF3FE", "1E40AF"
YELLOW, GREEN, GREEN_SOFT = "FDE68A", "047857", "ECFDF5"
RED, RED_SOFT, ORANGE_SOFT, GRAY_SOFT = "B91C1C", "FEF2F2", "FFF7ED", "F6F7F9"
WHITE, LIGHT = "FFFFFF", "D1D5DB"


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
         anchor=MSO_ANCHOR.TOP, mono=False, spacing=None, name=None):
    """parts: 문자열, 또는 줄마다 [(글, {옵션}), ...]의 목록. 옵션: size, color, bold, mono."""
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        box.name = name
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    lines = [[(parts, {})]] if isinstance(parts, str) else parts
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if spacing:
            p.space_after = Pt(spacing)
        for s, o in ([(line, {})] if isinstance(line, str) else line):
            r = p.add_run()
            r.text = s
            r.font.size = Pt(o.get("size", size))
            r.font.bold = o.get("bold", bold)
            r.font.color.rgb = rgb(o.get("color", color))
            _fonts(r, o.get("mono", mono))
    return box


def box(slide, x, y, w, h, fill, line=None, radius=0.08, name=None):
    shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
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


def chip(slide, x, y, label, size=15, h=0.4):
    """가명 토큰 칩 [고객1] — 이 발표의 반복 모양."""
    w = 0.16 + len(label) * size * 0.0105
    b = box(slide, x, y, w, h, YELLOW, radius=0.25, name=f"토큰 {label}")
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


def circle(slide, x, y, d, label, fill=BLUE, color=WHITE, size=16):
    c = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d))
    c.fill.solid()
    c.fill.fore_color.rgb = rgb(fill)
    c.line.fill.background()
    c.shadow.inherit = False
    tf = c.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = label
    r.font.size = Pt(size)
    r.font.bold = True
    r.font.color.rgb = rgb(color)
    _fonts(r)
    return c


def arrow(slide, x, y, w=0.35, h=0.3, color=SUB):
    a = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x), Inches(y), Inches(w), Inches(h))
    a.fill.solid()
    a.fill.fore_color.rgb = rgb(color)
    a.line.fill.background()
    a.shadow.inherit = False
    return a


def background(slide, hex_):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = rgb(hex_)


def title(slide, s):
    text(slide, 0.6, 0.42, 12.1, 0.75, s, size=30, bold=True, name="제목")


def page_no(slide, n, dark=False):
    text(slide, 12.2, 7.0, 0.6, 0.3, str(n), size=11, color=LIGHT if dark else SUB, align=PP_ALIGN.RIGHT)


def notes(slide, s):
    slide.notes_slide.notes_text_frame.text = s


# ── 장 ──

def s1_cover(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    background(s, NAVY)
    text(s, 0.8, 0.9, 11, 0.4, "인공지능프로젝트 결과물 발표 · 2026.11.17", size=14, color=LIGHT)
    text(s, 0.8, 1.55, 11, 1.2, [[("가림게이트", {"size": 60, "bold": True, "color": WHITE}),
                                   ("  GarimGate", {"size": 26, "color": "93C5FD"})]])
    text(s, 0.8, 2.85, 11.5, 0.6, "API 주소 한 줄로 붙이는 개인정보 보호 · 토큰 절약 게이트웨이", size=24, color="E5E7EB")
    # 반복 모양: 원문 → 가명 토큰
    box(s, 0.8, 4.05, 11.7, 1.75, "2B2D35", radius=0.06, name="원문과 나가는 글")
    text(s, 1.15, 4.3, 3.0, 0.4, "내가 쓴 요청", size=13, color=LIGHT)
    text(s, 3.2, 4.25, 9.0, 0.5, "김민수 고객(010-1234-5678)에게 배송 지연 사과 메일 써줘", size=18, color=WHITE)
    text(s, 1.15, 5.08, 3.0, 0.4, "외부 AI가 보는 글", size=13, color=LIGHT)
    x = chip(s, 3.2, 5.0, "[고객1]", size=16)
    text(s, x + 0.12, 5.04, 0.75, 0.4, "고객(", size=18, color=WHITE)
    x = chip(s, x + 0.8, 5.0, "[전화번호1]", size=16)
    text(s, x + 0.08, 5.04, 5.0, 0.4, ")에게 배송 지연 사과 메일 써줘", size=18, color=WHITE)
    text(s, 0.8, 6.55, 8, 0.4, "컴퓨터소프트웨어공학과  (이름)", size=14, color=LIGHT)
    notes(s, "(5초) 가림게이트입니다. API 주소 한 줄만 바꾸면 기존 AI 서비스에 개인정보 보호와 토큰 절약이 붙는 게이트웨이입니다. "
             "아래처럼 김민수 고객의 이름과 번호는 [고객1], [전화번호1]로 바뀌어서 외부 AI로 나갑니다.")


def s2_problem(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    title(s, "AI를 업무에 붙이면 생기는 세 가지 문제")
    cards = [
        ("1", "개인정보가 그대로 나간다", "고객 이름·전화번호·주민번호가 요청에 담긴 채 외부 AI 서버로 전송된다", RED_SOFT, RED),
        ("2", "쉬운 질문도 비싼 모델로", "“배송 언제 와요?”까지 고성능 모델로 보내 비용과 지연이 커진다", ORANGE_SOFT, "C2410C"),
        ("3", "막는 도구는 무겁다", "무거운 로컬 LLM과 복잡한 설정이 필요해 중소기업이 도입하기 어렵다", GRAY_SOFT, "374151"),
    ]
    for i, (n, head, body, fill, accent) in enumerate(cards):
        x = 0.6 + i * 4.1
        box(s, x, 1.55, 3.85, 3.9, fill, name=f"문제 {n}")
        circle(s, x + 0.35, 1.9, 0.62, n, fill=accent, size=20)
        text(s, x + 0.35, 2.8, 3.2, 0.9, head, size=21, bold=True)
        text(s, x + 0.35, 3.65, 3.2, 1.6, body, size=16, color="374151")
    text(s, 0.6, 5.85, 12, 0.5, [[("대상  ", {"bold": True, "color": BLUE}),
                                   ("AI 챗봇·AI 기능을 운영하는 중소기업·스타트업 개발팀, 사내 AI 사용을 관리하는 보안 담당자", {})]], size=16)
    page_no(s, 2)
    notes(s, "(30초) 기업이 AI를 쓰면 세 가지 문제가 생깁니다. 첫째, 고객 개인정보가 그대로 외부 AI 서버로 갑니다. "
             "둘째, 배송 문의 같은 쉬운 질문까지 비싼 모델로 보내 비용이 샙니다. 셋째, 이걸 막는 도구는 로컬 LLM이 필요해 무겁습니다. "
             "그래서 AI 기능을 운영하는 중소기업 개발팀과 보안 담당자를 대상으로 만들었습니다.")


def s3_solution(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    title(s, "API 주소만 바꾸면, 가리고 · 판단하고 · 되돌린다")
    # 영역: 이 PC 안 / 밖
    box(s, 2.45, 1.5, 3.3, 2.6, GREEN_SOFT, radius=0.05, name="이 PC 안")
    box(s, 6.15, 1.5, 4.15, 2.6, BLUE_SOFT, radius=0.05, name="밖")
    box(s, 10.7, 1.5, 2.0, 2.6, GREEN_SOFT, radius=0.05, name="이 PC 안 (복원)")
    text(s, 2.6, 1.62, 3.1, 0.35, "이 PC 안 — 원본은 여기서만", size=12, bold=True, color=GREEN)
    text(s, 6.3, 1.62, 3.9, 0.35, "밖 — 가명화된 문장만", size=12, bold=True, color=BLUE_INK)
    text(s, 10.85, 1.62, 1.8, 0.35, "이 PC 안", size=12, bold=True, color=GREEN)
    steps = [  # (x, 제목, 설명)
        (0.6, "기존 서비스", "API 주소만 게이트웨이로"),
        (2.6, "① 가리기", "이름·번호·주소 → [고객1]"),
        (4.3, "② 로컬 판단", "기밀인가 (원문을 봄)"),
        (6.3, "③ 클라우드 판단", "원본 필요·위험·난이도"),
        (8.4, "④ 경로 → LLM", "정해 둔 답·저렴·고성능·막음"),
        (10.85, "⑤ 복원", "답변 속 [고객1] → 김민수"),
    ]
    widths = {0.6: 1.45, 2.6: 1.4, 4.3: 1.3, 6.3: 1.8, 8.4: 1.75, 10.85: 1.7}
    for x, head, desc in steps:
        w = widths[x]
        box(s, x, 2.15, w, 1.65, WHITE, line=LINE, radius=0.08, name=head)
        text(s, x + 0.12, 2.3, w - 0.24, 0.5, head, size=15, bold=True)
        text(s, x + 0.12, 2.85, w - 0.24, 0.85, desc, size=12, color=SUB)
    for ax in (2.1, 4.02, 5.82, 8.12, 10.37):
        arrow(s, ax, 2.83, w=0.26, h=0.26)
    strengths = [
        ("보안", "원본은 이 PC 밖으로 안 나간다. 외부에는 가명 토큰만", GREEN),
        ("토큰 절약", "쉬운 질문은 정해 둔 답이나 저렴한 모델로", BLUE),
        ("간편함", "로컬 LLM 없이 CPU만, 설치 스크립트 하나, 설정은 화면에서", "374151"),
    ]
    for i, (head, body, accent) in enumerate(strengths):
        x = 0.6 + i * 4.1
        box(s, x, 4.55, 3.85, 1.85, GRAY_SOFT, name=f"강점 {head}")
        text(s, x + 0.3, 4.75, 3.3, 0.5, head, size=20, bold=True, color=accent)
        text(s, x + 0.3, 5.3, 3.3, 1.0, body, size=15, color="374151")
    page_no(s, 3)
    notes(s, "(40초) 구조입니다. 기존 서비스는 API 주소만 게이트웨이로 바꿉니다. 이 PC 안에서 먼저 개인정보를 [고객1] 같은 토큰으로 가리고, "
             "원문을 봐야 하는 기밀 판단은 로컬 모델이 합니다. 밖으로는 가명화된 문장만 나가서, 클라우드 판단이 원본 필요·위험·난이도를 보고 "
             "정해 둔 답, 저렴한 모델, 고성능 모델, 막음 중 경로를 고릅니다. 돌아온 답변의 토큰은 이 PC에서 원래 값으로 되돌립니다. "
             "그래서 보안, 토큰 절약, 간편함 세 가지를 한 번에 얻습니다.")


def s4_ai_roles(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    title(s, "기능마다 생성형 AI가 하는 일")
    rows = [
        ("기능", "AI가 하는 일", "코드가 하는 일"),
        ("가명화", "이름 인식 모델이 사전에 없는 이름·주소의 위치를 찾는다 (로컬, 14.7MB)", "정규식·사전으로 찾아 [고객1]로 바꾸고, 답변에서 되돌린다"),
        ("기밀 판단", "판단 모델이 원문을 보고 ‘기밀인가’의 확률을 낸다 (로컬, CPU)", "기준값과 비교해 엄격 수준에서 막는다"),
        ("라우팅", "판단 API가 가명화된 문장으로 원본 필요·위험·난이도·의도를 판단한다", "판단 결과로 템플릿·저렴·고성능·막음을 고른다"),
        ("답변", "LLM이 가명 토큰이 든 요청으로 답을 쓴다", "토큰을 원래 이름·번호로 되돌린다 (조사까지 맞춤)"),
        ("FAQ", "쓰지 않는다", "등록된 질문은 정해 둔 답으로 바로 (비용 0)"),
    ]
    tbl = s.shapes.add_table(len(rows), 3, Inches(0.6), Inches(1.45), Inches(12.1), Inches(4.2)).table
    tbl.columns[0].width = Inches(1.7)
    tbl.columns[1].width = Inches(5.5)
    tbl.columns[2].width = Inches(4.9)
    for r, row in enumerate(rows):
        tbl.rows[r].height = Inches(0.62 if r == 0 else 0.7)
        for c, val in enumerate(row):
            cell = tbl.cell(r, c)
            cell.fill.solid()
            cell.fill.fore_color.rgb = rgb(BLUE if r == 0 else (BLUE_SOFT if c == 1 else WHITE))
            cell.margin_left = cell.margin_right = Inches(0.15)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            run = p.add_run()
            run.text = val
            run.font.size = Pt(15 if r == 0 else 14)
            run.font.bold = r == 0 or c == 0
            run.font.color.rgb = rgb(WHITE if r == 0 else INK)
            _fonts(run)
    box(s, 0.6, 5.95, 12.1, 0.75, NAVY, radius=0.12, name="원칙")
    text(s, 0.6, 5.95, 12.1, 0.75, [[("판단은 판단 모델", {"color": "93C5FD", "bold": True}), ("   ·   ", {"color": LIGHT}),
                                      ("생성은 LLM", {"color": YELLOW, "bold": True}), ("   ·   ", {"color": LIGHT}),
                                      ("가리기·복원·계산은 코드", {"color": WHITE, "bold": True})]],
         size=19, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    page_no(s, 4)
    notes(s, "(40초) 기능마다 생성형 AI의 역할입니다. 가명화에서는 이름 인식 모델이 사전에 없는 이름과 주소의 위치를 찾습니다. "
             "기밀 판단은 로컬 판단 모델이, 라우팅 판단은 가명화된 문장만 받은 판단 API가 합니다. 답변은 LLM이 가명 토큰이 든 요청으로 씁니다. "
             "원칙은 판단은 판단 모델, 생성은 LLM, 실제로 값을 바꾸고 되돌리고 계산하는 일은 코드가 정확하게 한다는 것입니다.")


def s5_demo(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    title(s, "시연 (30초)")
    pic = s.shapes.add_picture(str(DEMO_SHOT), Inches(0.6), Inches(1.4), width=Inches(7.6))
    pic.name = "시연 화면 (녹화 영상으로 바꾸기)"
    pic.line.color.rgb = rgb(LINE)
    pic.line.width = Pt(1)
    steps = [
        ("1", [("“주문한 거 배송 언제 와요?”", {"bold": True})], "LLM 없이 정해 둔 답 · 비용 0"),
        ("2", [("“김민수 고객(010-…)에게 사과 메일”", {"bold": True})], None),
        ("3", [("“이 주민번호로 나이 계산해줘”", {"bold": True})], "실제 값이 필요 → 밖으로 안 보내고 안내"),
        ("4", [("비용 카드", {"bold": True})], "기존 방식(전부 고성능) vs 게이트웨이"),
    ]
    y = 1.5
    for n, head, desc in steps:
        circle(s, 8.6, y, 0.48, n, size=15)
        text(s, 9.25, y - 0.02, 3.5, 0.45, [head], size=15)
        if n == "2":
            text(s, 9.25, y + 0.45, 1.3, 0.4, "밖으로는", size=13, color=SUB)
            x = chip(s, 10.15, y + 0.42, "[고객1]", size=13, h=0.34)
            text(s, x + 0.1, y + 0.45, 2.4, 0.4, "답변엔 김민수", size=13, color=SUB)
        else:
            text(s, 9.25, y + 0.45, 3.5, 0.6, desc, size=13, color=SUB)
        y += 1.3
    page_no(s, 5)
    notes(s, "(30초 영상) 녹화 영상으로 바꿔 넣는다: 사진을 지우고 [삽입 → 비디오 → 이 디바이스]로 30초 녹화 파일을 넣고, "
             "[재생 → 시작: 자동]으로 둔다. 대본은 docs/presentation/demo-script.md. "
             "말할 것: 첫째, 배송 문의는 LLM 없이 정해 둔 답이 나옵니다. 둘째, 사과 메일은 밖으로 [고객1]만 나가고 받은 답변에는 김민수가 돌아옵니다. "
             "셋째, 주민번호로 나이를 계산해 달라는 요청은 실제 값이 필요해서 밖으로 보내지 않고 안내합니다. 위 카드에서 비용 차이가 보입니다.")


def s6_results(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    title(s, "측정 결과")
    text(s, 0.6, 1.2, 12, 0.4, "테스트셋 190행(업종 4개) + 생성 데이터 622개 · 이름·번호는 모두 가상 값 · 2026-11-08 측정", size=13, color=SUB)
    stats = [
        ("99.5%", "개인정보 가명화", "619 / 622 · 전화·이메일·주민·카드·계좌·주소 100%", GREEN, GREEN_SOFT),
        ("100%", "실제 값이 필요한 요청 막기", "18 / 18 · 잘못 막음 0.6%", RED, RED_SOFT),
        ("89%", "경로 정확도", "190행 중 169 · 쇼핑몰(시연 업종) 97%", BLUE, BLUE_SOFT),
        ("95~100%", "쉬운 요청 비용 절감", "정해 둔 답 100% · 저렴한 모델 95%", "B45309", ORANGE_SOFT),
    ]
    for i, (big, label, detail, color, fill) in enumerate(stats):
        x = 0.6 + i * 3.075
        box(s, x, 1.85, 2.85, 3.3, fill, name=f"숫자 {label}")
        text(s, x + 0.25, 2.1, 2.45, 1.0, big, size=46 if len(big) < 6 else 38, bold=True, color=color)
        text(s, x + 0.25, 3.25, 2.45, 0.8, label, size=17, bold=True)
        text(s, x + 0.25, 4.05, 2.45, 1.0, detail, size=13, color="374151")
    facts = [("판단 지연", "평균 0.50초 · p95 0.65초"), ("이 PC에서 도는 모델", "판단 모델(CPU) + 이름 인식 14.7MB, GPU 없음"),
             ("전체 비용 절감", "요청 구성에 달림 — 복잡한 요청 10%면 48%, 이 테스트셋은 10%")]
    for i, (k, v) in enumerate(facts):
        y = 5.45 + i * 0.45
        text(s, 0.6, y, 12.1, 0.4, [[(f"{k}   ", {"bold": True, "color": BLUE_INK}), (v, {})]], size=14)
    page_no(s, 6)
    notes(s, "(40초) 결과입니다. 개인정보 가명화는 99.5퍼센트, 이름이 아닌 번호와 주소는 100퍼센트 가렸습니다. "
             "실제 값이 필요한 요청은 18건 모두 막았고 잘못 막은 것은 0.6퍼센트입니다. 경로 정확도는 89퍼센트, 시연 업종인 쇼핑몰은 97퍼센트입니다. "
             "쉬운 요청은 95에서 100퍼센트 비용을 아낍니다. 전체 절감률은 요청 구성에 달려서, 복잡한 요청이 많은 이번 테스트셋은 10퍼센트였습니다. "
             "판단은 평균 0.5초, GPU 없이 CPU만으로 돕니다.")


def s7_ai_dev(prs, nums):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    title(s, "개발에 AI 활용 — AI에게 맡긴 것, 내가 결정한 것")
    left = ["코드와 시험 작성 — 기능마다 시험을 함께, 고장 내서 시험이 잡는지 확인",
            "측정 실행기와 결과 분석 — 테스트셋 190행, 가명화·경로·비용",
            "새 도구는 공식 문서부터 확인 — Ollaya, OpenAI, Cloudflare",
            "커밋마다 자동 보안 검토 — 지적을 받아 고침 (주소만 믿는 관리 기능 등)"]
    right = ["판단을 둘로 나누기 — 원문이 필요한 판단만 이 PC, 나머지는 가명화 문장으로 클라우드",
             "무엇을 막고 무엇을 보낼지 — 보안 수준, 기준값, 주소 처리",
             "화면 설계 — 써 보고 세 번 고침 (스크롤 없이, 터미널 입력, 설정은 화면에서)",
             "범위 줄이기 — 파인튜닝 보류, 설문은 웹 체험판으로"]
    for i, (head, items, fill, accent) in enumerate([("AI(Claude Code)에게 맡긴 일", left, BLUE_SOFT, BLUE_INK),
                                                     ("내가 판단하고 결정한 일", right, GRAY_SOFT, INK)]):
        x = 0.6 + i * 6.15
        box(s, x, 1.45, 5.95, 3.85, fill, name=head)
        text(s, x + 0.3, 1.65, 5.4, 0.5, head, size=19, bold=True, color=accent)
        text(s, x + 0.3, 2.3, 5.4, 2.9, [[("· ", {"bold": True, "color": accent}), (t, {})] for t in items],
             size=15, spacing=14)
    strip = [(nums["days"], "커밋한 날"), (nums["decisions"], "설계 결정 기록"), (nums["ai_rows"], "AI 활용 기록"),
             (nums["tests"], "자동 시험")]
    for i, (v, k) in enumerate(strip):
        x = 0.6 + i * 3.075
        text(s, x, 5.7, 2.9, 0.7, v, size=32, bold=True, color=BLUE)
        text(s, x, 6.4, 2.9, 0.4, k, size=13, color=SUB)
    page_no(s, 7)
    notes(s, "(30초) 개발에도 AI를 적극 썼습니다. 코드와 시험 작성, 측정, 공식 문서 확인, 커밋마다의 보안 검토는 Claude Code에 맡겼습니다. "
             "대신 원문이 필요한 판단만 이 PC에서 하고 나머지는 가명화된 문장으로 클라우드에 맡기는 구조, 무엇을 막을지의 정책, 화면 설계, "
             "범위를 줄이는 결정은 제가 했습니다. 모든 결정과 AI 활용은 기록으로 남겼습니다.")


def s8_limits(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    title(s, "한계와 다음 단계")
    box(s, 0.6, 1.45, 5.95, 5.0, RED_SOFT, name="한계")
    text(s, 0.9, 1.65, 5.4, 0.5, "한계", size=19, bold=True, color=RED)
    text(s, 0.9, 2.25, 5.4, 1.0, [[("33%", {"size": 48, "bold": True, "color": RED}),
                                    ("  개인정보가 든 요청 중 막힌 비율", {"size": 15, "bold": True})]])
    text(s, 0.9, 3.35, 5.4, 1.2, "테스트셋 19 / 57. 답이 실제 값에 달린 요청(주민번호로 나이 계산, 주소 확인)은 가린 글로 풀 수 없어 "
                                 "밖으로 보내지 않고 막는다. 안전하지만 사용자는 그 일을 못 한다", size=14, color="374151")
    text(s, 0.9, 4.6, 5.4, 1.5, [[("· ", {"bold": True, "color": RED}), ("이름 인식 99% — 붙여 쓴 이름은 놓칠 수 있다", {})],
                                   [("· ", {"bold": True, "color": RED}), ("전체 비용 절감은 요청 구성에 달린다", {})]],
         size=14, spacing=6)
    box(s, 6.75, 1.45, 5.95, 5.0, BLUE_SOFT, name="다음 단계")
    text(s, 7.05, 1.65, 5.4, 0.5, "다음 단계", size=19, bold=True, color=BLUE_INK)
    nexts = [("완전 로컬 모드", "막힌 요청은 밖으로 보내지 않고 이 PC의 작은 LLM이 처리"),
             ("계산은 코드로", "나이·날짜 계산은 원본을 PC 안에서 코드가 풀고 결과만 LLM에"),
             ("배포·사용자 평가", "웹 체험판 준비 완료 → 12월 사용자 설문 10명 이상")]
    y = 2.3
    for i, (head, body) in enumerate(nexts, 1):
        circle(s, 7.05, y, 0.5, str(i), size=15)
        text(s, 7.75, y - 0.02, 4.7, 0.45, head, size=16, bold=True)
        text(s, 7.75, y + 0.45, 4.7, 0.8, body, size=14, color="374151")
        y += 1.3
    page_no(s, 8)
    notes(s, "(25초) 한계도 있습니다. 개인정보가 든 요청의 3분의 1은 답이 실제 값에 달려 있어서, 가린 글로는 풀 수 없으니 막습니다. "
             "안전하지만 사용자는 그 일을 못 합니다. 다음 단계로 이런 요청을 이 PC의 작은 LLM이 처리하는 완전 로컬 모드와, "
             "정해진 계산은 코드가 PC 안에서 푸는 방법을 준비하고 있습니다. 사용자 평가는 웹 체험판으로 12월에 진행합니다.")


def s9_end(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    background(s, NAVY)
    text(s, 0.8, 2.0, 11.7, 1.1, "가림게이트", size=54, bold=True, color=WHITE)
    text(s, 0.8, 3.15, 11.7, 0.6, "보안을 지키면서 아끼는 관문 — API 주소 한 줄로", size=24, color="E5E7EB")
    x = chip(s, 0.8, 4.25, "[고객1]", size=16)
    x = chip(s, x + 0.15, 4.25, "[전화번호1]", size=16)
    chip(s, x + 0.15, 4.25, "[주소1]", size=16)
    text(s, 0.8, 5.45, 11, 0.4, "github.com/baesanggue/ai-gateway", size=16, color="93C5FD")
    text(s, 0.8, 6.3, 11, 0.5, "감사합니다", size=20, bold=True, color=WHITE)
    notes(s, "(5초) 가림게이트, 보안을 지키면서 아끼는 관문이었습니다. 감사합니다.")


def main() -> None:
    nums = repo_numbers()
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    prs.core_properties.title = "가림게이트 결과물 발표"
    s1_cover(prs)
    s2_problem(prs)
    s3_solution(prs)
    s4_ai_roles(prs)
    s5_demo(prs)
    s6_results(prs)
    s7_ai_dev(prs, nums)
    s8_limits(prs)
    s9_end(prs)
    prs.save(OUT)
    print(f"saved {OUT} ({nums})")


if __name__ == "__main__":
    main()
