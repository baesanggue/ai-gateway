"""12/7 제출용 사용자 평가 보고서(PDF 1개)를 만든다 (#129).

    python docs/presentation/build_survey_report.py     # 시스템 파이썬(python-pptx 1.0, Pillow, openpyxl, pypdf)으로 실행

- 제출 조건(개발 완성도 8·10점): 고객(테스터) 확보 과정 스크린샷 + 분석 결과(엑셀, 그래프)를 PDF 하나로. 발표 자료를 그대로 쓰지 않는다
- 입력은 테스트기록/ 폴더(응답 엑셀, 모집 메시지 캡처 3장, 설문지 PDF)다. 응답자 이름과 대화가 있어 커밋하지 않는다(.gitignore)
- 개인정보를 넣지 않는다: 응답자는 R1~R10으로만 쓰고 이름 열은 읽지 않는다. 캡처의 체험판 비밀번호·개인 이름·프로필 사진은 가린다(MASKS)
- A4 세로 7쪽을 PowerPoint로 PDF로 바꾸고, 뒤에 설문지 PDF를 부록으로 붙인다. 결과물(pptx, pdf)은 테스트기록/에만 둔다
- 디자인은 발표 자료와 같다(build_deck.py의 그리기 도구·색·글씨 크기, #126): 검정 글씨, 색 머리 띠 상자, 크기 6가지
- 주관식 요약과 개선 계획은 11/17 응답을 읽고 정리한 것이다. 응답이 바뀌면 SUMMARY·PLANS도 다시 본다
"""
import statistics
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from openpyxl import load_workbook
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt
from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_deck import (AMBER, BAND, BLUE, DARK, FONT, GREEN, HANG, INK, RED, T_BODY, T_HEAD, T_SMALL,  # noqa: E402
                        T_STAT, T_TITLE, TINT, WHITE, _fonts, card, check_text, label_row, rect, rgb, text)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "테스트기록"
XLSX = SRC / "가림게이트 체험판 설문 (약 10분)(응답).xlsx"
FORM_PDF = SRC / "가림게이트 체험판 설문 (약 10분) - Google Forms.pdf"
OUT_PPTX = SRC / "가림게이트_사용자평가_배상규.pptx"
OUT_PDF = SRC / "가림게이트_사용자평가_배상규.pdf"

PAGE_W, PAGE_H = 8.27, 11.69          # A4 세로
M = 0.6                                # 좌우 여백
PW = PAGE_W - 2 * M                    # 본문 폭
CT = 2.05                              # 본문이 시작하는 높이 (제목이 두 줄이어도 겹치지 않게)

# 캡처에서 가릴 곳 (픽셀, 왼쪽·위·오른쪽·아래). 좌표 눈금을 그려 보고 정했다(11/17)
SHOTS = [  # (파일, 띠 제목)
    ("단톡 메시지.png", "팀 단톡방 (9명)"),
    ("개인 메시지 1.png", "개인 메시지 (1명)"),
    ("가족 메시지.png", "가족 단톡방 (3명)"),
]
MASKS = {
    "단톡 메시지.png": [(0, 22, 48, 72), (150, 264, 242, 285)],                      # 프로필 사진, 비밀번호
    "개인 메시지 1.png": [(55, 24, 112, 50), (155, 299, 242, 318)],                  # 이름, 비밀번호
    "가족 메시지.png": [(0, 0, 50, 50), (50, 0, 206, 26), (0, 106, 50, 150), (55, 108, 106, 128),
                     (150, 330, 242, 349)],                                          # 사진·이름(머리, 보낸 사람), 비밀번호
}
ROUTES = [("팀 프로젝트 단톡방", "9명"), ("가족 단톡방", "3명"), ("개인 메시지", "1명")]   # 캡처 기준 요청 인원

# 점수 문항(엑셀 6~22열)의 짧은 이름. 번호는 엑셀 열 번호이고, 보고서의 그래프·표에 같은 번호를 쓴다
ITEMS = {
    6: "첫 화면에서 할 일을 알기 쉬움", 7: "화면의 각 칸을 이해하기 쉬움", 8: "주소만 바꾸는 도입이 쉬워 보임",
    9: "나간 글에서 가린 것을 확인함", 10: "이름·번호가 바르게 되돌아옴", 11: "실제 값 필요 요청 막기가 적절",
    12: "유출 걱정이 줄 것 같음", 13: "쉬운 질문 처리를 화면에서 확인", 14: "비용 절감 표시가 이해하기 쉬움",
    15: "처리 과정 표시가 믿음을 줌", 16: "응답 시간이 만족스러움", 17: "파일 검사가 유용함 (선택)",
    18: "회사에서 쓰고 싶음", 19: "전반적으로 만족함", 20: "막히는 요청이 불편 (역문항)",
    21: "막힌 요청의 PC 안 처리를 원함", 22: "다른 업무에도 쓸 수 있음",
}
REVERSE = {20}                          # 역문항: 높을수록 한계가 크다
JOB_SHORT = {"학생 (IT전공)": "학생 (IT 전공)", "직장인 (그 외 직무)": "직장인 (그 외)", "직장인(개발/IT)": "직장인 (개발·IT)",
             "it전공 학생 및 it계열 직장인": "IT 전공 학생 겸 IT 직장인"}
DONE_SHORT = {"배송 문의(정해 둔 답)": "배송 문의", "사과 메일 (가명화/복원)": "사과 메일 (가명·복원)",
              "주민번호로 나이 계산(막힘)": "주민번호 나이 계산 (막힘)", "가상의 이름/주소로 자유 입력": "자유 입력",
              "파일 검사": "파일 검사", "현황/요청 기록 보기": "현황·요청 기록"}

# 주관식 요약 (11/17 응답 R1~R10을 읽고 주제별로 셌다. 한 응답이 두 주제에 들 수 있다)
SUMMARY = [
    ("좋았던 점", GREEN, [("개인정보를 가려 줌", 7), ("파일 검사", 1), ("요청 앞단에서 처리하는 게이트웨이 구조", 1),
                       ("바로 작성되어 편함", 1), ("무응답", 1)]),
    ("불편했던 점", RED, [("처음에 무엇을 해야 할지 모름 (안내 부족)", 5), ("화면이 보기 힘듦", 2), ("비용 표시를 알기 어려움", 1),
                        ("API 문서·코드 연동 예시가 필요함", 1), ("시연 화면에서 긴 요청이 잘려 보임", 1),
                        ("일반 낱말(기린)을 이름으로 가림", 1), ("무응답", 1)]),
    ("가려지지 않은 정보·이상한 답", DARK, [("없음", 6), ("답을 찾을 수 없다고 나옴", 1), ("사과 메일 내용을 지어냄", 1),
                                    ("일반 낱말(기린)을 이름으로 가림", 1), ("무응답", 1)]),
]
PLANS = [  # 12월 경진대회 전 개선 계획 (아직 하지 않은 일)
    ("첫 화면 안내", "처음 쓰는 사람을 위해 3단계 안내를 넣고, 시연 순서를 번호로 보여 줍니다."),
    ("비용 표시", "“전부 비싼 모델로 보냈다면 얼마, 가림게이트로 얼마”처럼 문장으로 바꿉니다."),
    ("개발자 안내", "주소 한 줄을 바꾸는 코드 예시와 API 설명 화면을 붙입니다."),
    ("가리기 정확도", "긴 요청은 줄임표로 보여 주고, “기린” 같은 일반 낱말은 이름이 아닌 낱말 목록에 넣습니다."),
]


# ── 데이터 ──

def load() -> tuple[list, list]:
    """응답 엑셀을 읽는다. 이름 열(2열)은 쓰지 않는다."""
    ws = load_workbook(XLSX, read_only=True, data_only=True).worksheets[0]
    rows = [r for r in ws.iter_rows(values_only=True) if any(v not in (None, "") for v in r)]
    header, data = list(rows[0]), [list(r) for r in rows[1:]]
    for r in data:
        r[1] = None                     # 이름은 메모리에서도 지운다
    return header, data


def scores(data, col: int) -> list[float]:
    return [r[col - 1] for r in data if isinstance(r[col - 1], (int, float))]


def masked_shots(tmp: Path) -> list[tuple[Path, str, tuple[int, int]]]:
    out = []
    for name, title in SHOTS:
        im = Image.open(SRC / name).convert("RGB")
        d = ImageDraw.Draw(im)
        for box in MASKS[name]:
            d.rectangle(box, fill=(17, 24, 39))
        p = tmp / f"masked-{len(out)}.png"
        im.save(p)
        out.append((p, title, im.size))
    return out


# ── 그리기 도구 (A4 세로) ──

def page(prs, kicker: str, title_: str, n: int):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    text(s, M, 0.45, PW, 0.3, kicker, size=T_SMALL, bold=True, color=BLUE)
    text(s, M, 0.8, PW, 1.1, title_, size=T_TITLE, bold=True, line=1.05)
    text(s, PAGE_W - M - 0.6, PAGE_H - 0.5, 0.6, 0.3, str(n), size=T_SMALL, align=PP_ALIGN.RIGHT)
    return s


def bar_chart(slide, x, y, w, h, cats, vals, fmt="0", max_v=None, colors=None):
    """가로 막대 그래프(위에서 아래로 cats 순서). 값 축·눈금선 없이 막대 끝에 값을 쓴다."""
    cd = CategoryChartData()
    cd.categories = cats
    cd.add_series("값", vals)
    ch = slide.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(x), Inches(y), Inches(w), Inches(h), cd).chart
    ch.has_title = False                # 계열이 하나면 계열 이름("값")이 제목으로 붙는다
    ch.has_legend = False
    ch.font.size = Pt(T_SMALL)
    ch.font.name = FONT
    ch.font.color.rgb = rgb(INK)
    plot = ch.plots[0]
    plot.gap_width = 55
    plot.has_data_labels = True
    dl = plot.data_labels
    dl.number_format, dl.number_format_is_linked = fmt, False
    dl.position = XL_LABEL_POSITION.OUTSIDE_END
    dl.font.size, dl.font.bold = Pt(T_SMALL), True
    dl.font.color.rgb = rgb(INK)
    va = ch.value_axis
    va.has_major_gridlines = False
    va.minimum_scale = 0
    if max_v:
        va.maximum_scale = max_v
    va.visible = False
    ca = ch.category_axis
    ca.reverse_order = True
    ca.tick_labels.font.size = Pt(T_SMALL)
    ca.tick_labels.font.color.rgb = rgb(INK)
    ca.format.line.color.rgb = rgb(INK)
    # 항목 이름을 하나도 건너뛰지 않는다(PowerPoint는 자리가 좁다고 보면 하나씩 건너뛴다). 순서: noMultiLvlLbl 앞
    cat_ax = ca._element
    if cat_ax.find(qn("c:tickLblSkip")) is None:
        skip = cat_ax.makeelement(qn("c:tickLblSkip"), {"val": "1"})
        anchor = cat_ax.find(qn("c:noMultiLvlLbl"))
        anchor.addprevious(skip) if anchor is not None else cat_ax.append(skip)
    ser = plot.series[0]
    for i in range(len(vals)):
        fill = ser.points[i].format.fill
        fill.solid()
        fill.fore_color.rgb = rgb(colors[i] if colors else BAND)
    return ch


def table(slide, x, y, col_w, row_h, rows, align=None, bold_first_col=True, margin=0.06):
    """머리 줄을 채운 표 (#126 규칙: 회색 선 대신 줄마다 바탕색을 번갈아). 바깥 테두리는 띠 색.
    margin: 칸 안 좌우 여백(인치). 좁은 숫자 칸은 줄여야 '4.3'이 한 줄에 들어간다."""
    n_r, n_c = len(rows), len(rows[0])
    gf = slide.shapes.add_table(n_r, n_c, Inches(x), Inches(y), Inches(sum(col_w)), Inches(row_h * n_r))
    tbl = gf.table
    tbl.first_row, tbl.horz_banding = True, False
    for j, w in enumerate(col_w):
        tbl.columns[j].width = Inches(w)
    for i in range(n_r):
        tbl.rows[i].height = Inches(row_h)
        for j in range(n_c):
            c = tbl.cell(i, j)
            c.fill.solid()
            c.fill.fore_color.rgb = rgb(BAND if i == 0 else (TINT if i % 2 == 0 else WHITE))
            c.margin_left = c.margin_right = Inches(margin)
            c.margin_top = c.margin_bottom = Inches(0.02)
            c.vertical_anchor = MSO_ANCHOR.MIDDLE
            p = c.text_frame.paragraphs[0]
            p.alignment = (align[j] if align else PP_ALIGN.LEFT)
            r = p.add_run()
            r.text = str(rows[i][j])
            r.font.size = Pt(T_SMALL)
            r.font.bold = i == 0 or (bold_first_col and j == 0)
            r.font.color.rgb = rgb(WHITE if i == 0 else INK)
            _fonts(r)
    rect(slide, x, y, sum(col_w), row_h * n_r, None, line=BAND, line_w=1.5, name="표 테두리")
    return y + row_h * n_r


def bullets(slide, x, y, w, h, items, color=INK, size=T_BODY, spacing=6):
    text(slide, x, y, w, h, [[("·  ", {"bold": True, "color": color}), (t, {})] for t in items],
         size=size, spacing=spacing, line=1.2, hang=HANG)


# ── 쪽 ──

def p1_summary(prs, header, data):
    s = page(prs, "인공지능프로젝트 결과물 · 사용자 평가", "가림게이트 사용자 평가 결과", 1)
    text(s, M, 1.55, PW, 0.35, "컴퓨터소프트웨어공학과 배상규 · github.com/baesanggue/ai-gateway", size=T_SMALL)
    n = len(data)
    sat, want = statistics.mean(scores(data, 19)), statistics.mean(scores(data, 18))
    n_q = len(header) - 2                                   # 타임스탬프·이름을 뺀 질문 수
    stats = [("테스터", f"{n}명", "11월 14~16일에 응답했습니다.", BLUE),
             ("질문", f"{n_q}개", "5점 척도 17개가 들어 있습니다.", BLUE),
             ("전반적 만족도", f"{sat:.1f} / 5", f"{n}명의 평균입니다.", GREEN),
             ("회사에서 쓰고 싶다", f"{want:.1f} / 5", f"{n}명의 평균입니다.", GREEN)]
    cw, ch_, gap = (PW - 0.3) / 2, 1.75, 0.3
    for i, (head, big, note, color) in enumerate(stats):
        x, y = M + (i % 2) * (cw + gap), CT - 0.15 + (i // 2) * (ch_ + 0.25)
        bx, by, bw = card(s, x, y, cw, ch_, head)
        text(s, bx, by - 0.05, bw, 0.7, big, size=T_STAT, bold=True, color=color)
        text(s, bx, by + 0.68, bw, 0.4, note, size=T_SMALL)
    rows = [("무엇을", "AI 서비스로 가는 요청의 개인정보를 가명으로 바꿔 보내고, 받은 답에서 되돌리는 게이트웨이입니다."),
            ("배포", "관리·시연 화면을 웹 체험판으로 공개했습니다. Cloudflare 터널과 비밀번호로 보호했습니다."),
            ("기간", "11월 14일에 공개하고 11월 17일에 마감했습니다. 응답은 11월 14~16일에 받았습니다."),
            ("해 본 것", "시연 4가지(배송 문의, 사과 메일, 주민번호 나이 계산, 자유 입력)와 파일 검사입니다."),
            ("설문", "Google Forms로 받았습니다. 설문지 전체는 마지막 부록에 붙였습니다.")]
    y = 6.0
    text(s, M, y, PW, 0.4, "평가 개요", size=T_HEAD, bold=True)
    y += 0.5
    for k, v in rows:
        bx = label_row(s, M, y, PW, 0.7, k, 1.35)
        text(s, bx, y, M + PW - bx - 0.15, 0.7, v, size=T_SMALL, anchor=MSO_ANCHOR.MIDDLE, line=1.15)
        y += 0.78
    text(s, M, y + 0.1, PW, 0.6, "2쪽 고객 확보 과정 · 3쪽 응답자 · 4쪽 문항별 점수 · 5쪽 주관식 · 6쪽 분석과 개선 계획 · "
                                  "7쪽 응답 원자료 · 부록 설문지", size=T_SMALL, line=1.2)


def p2_recruit(prs, data, shots):
    s = page(prs, "1. 고객(테스터) 확보", "테스터를 이렇게 모았습니다", 2)
    asked = sum(int(n.rstrip("명")) for _, n in ROUTES)
    text(s, M, CT - 0.4, PW, 0.9,
         f"11월 14일에 체험판 주소·비밀번호·설문 링크를 담은 같은 안내 메시지를 세 곳에 보냈습니다. "
         f"캡처 기준으로 {asked}명에게 요청했고, {len(data)}명이 응답했습니다({len(data) / asked:.0%}).", line=1.25)
    y = table(s, M, CT + 0.75, [3.6, 1.6, PW - 5.2], 0.36,
              [("보낸 곳", "요청 인원", "보낸 날")] + [(r, n, "11월 14일") for r, n in ROUTES] +
              [("합계", f"{asked}명", f"응답 {len(data)}명")],
              align=[PP_ALIGN.LEFT, PP_ALIGN.CENTER, PP_ALIGN.CENTER])
    days = Counter(str(r[0])[:10] for r in data)
    by_day = ", ".join(f"{int(d[5:7])}월 {int(d[8:])}일 {c}명" for d, c in sorted(days.items()))
    bx = label_row(s, M, y + 0.25, PW, 0.55, "날짜별 응답", 1.6)
    text(s, bx, y + 0.25, M + PW - bx - 0.15, 0.55, f"{by_day}이 응답했습니다.", size=T_SMALL, anchor=MSO_ANCHOR.MIDDLE)
    y += 1.05
    text(s, M, y, PW, 0.4, "보낸 안내 메시지 (비밀번호와 개인 이름·사진은 가렸습니다)", size=T_SMALL, bold=True)
    y += 0.4
    gap = 0.2
    cw = (PW - 2 * gap) / 3
    iw = cw - 0.2
    tallest = max(iw * h / w for _, _, (w, h) in shots)
    for i, (path, title, (w, h)) in enumerate(shots):
        x = M + i * (cw + gap)
        card(s, x, y, cw, 0.5 + 0.1 + tallest + 0.1, title, pad=0.1)
        s.shapes.add_picture(str(path), Inches(x + 0.1), Inches(y + 0.6), width=Inches(iw))


def p3_people(prs, header, data):
    s = page(prs, "2. 응답자", "응답자 7명이 IT 전공·직군입니다", 3)
    jobs = Counter(JOB_SHORT.get(r[2], r[2]) for r in data)
    freq = Counter(r[3] for r in data)
    done = Counter()
    for r in data:
        for part in str(r[4]).split(", "):
            done[DONE_SHORT.get(part.strip(), part.strip())] += 1
    blocks = [("현재 하는 일", jobs.most_common(), 1.6),
              ("생성형 AI를 쓰는 빈도", [(k, freq.get(k, 0)) for k in ("매일", "일주일에 몇 번", "한 달에 몇 번")], 1.2),
              ("체험판에서 해 본 것 (여러 개 고름)", done.most_common(), 2.2)]
    y = CT - 0.15
    for title, pairs, h in blocks:
        bx, by, bw = card(s, M, y, PW, h + 0.75, title)
        bar_chart(s, bx, by - 0.05, bw, h, [k for k, _ in pairs], [v for _, v in pairs], fmt='0"명"',
                  max_v=max(v for _, v in pairs) * 1.25)
        y += h + 0.95
    text(s, M, y, PW, 1.4, "응답자 대부분이 생성형 AI를 매일 쓰는 IT 쪽 사람이라, API 문서·코드 연동 예시 같은 개발자 관점의 의견이 "
                           "나왔습니다. IT가 아닌 응답자는 3명뿐이라, 비전공자의 평가는 더 받아야 합니다.", line=1.25)


def p4_items(prs, data):
    s = page(prs, "3. 문항별 점수", "보안 기능은 높게, 첫 화면 안내와 비용 표시는 낮게 평가했습니다", 4)
    cats, vals, colors = [], [], []
    for col, label in ITEMS.items():
        v = statistics.mean(scores(data, col))
        cats.append(f"{col}. {label}")
        vals.append(round(v, 1))
        colors.append(DARK if col in REVERSE else (AMBER if v < 3.5 else BAND))
    n17 = len(scores(data, 17))
    bx, by, bw = card(s, M, CT - 0.15, PW, 7.9, f"5점 척도 평균 ({len(data)}명, 17번은 해 본 {n17}명)")
    bar_chart(s, bx, by - 0.05, bw, 7.2, cats, vals, fmt="0.0", max_v=5.6, colors=colors)
    y = CT - 0.15 + 8.05
    text(s, M, y, PW, 0.9, [[("파랑", {"bold": True, "color": BLUE}), (": 3.5점 이상    ", {}),
                             ("주황", {"bold": True, "color": AMBER}), (": 3.5점 미만, 고칠 곳    ", {}),
                             ("검정", {"bold": True}), (": 20번은 역문항이라 높을수록 막히는 요청이 불편하다는 뜻입니다.", {})]],
         size=T_SMALL, line=1.25)


def p5_open(prs, data):
    s = page(prs, "4. 주관식", "주관식 응답을 주제별로 셌습니다", 5)
    y = CT - 0.15
    for title, band, pairs in SUMMARY:
        h = 0.5 + 0.15 + 0.27 * len(pairs) + 0.2           # 14pt 줄 간격 1.25 ≈ 0.25"
        bx, by, bw = card(s, M, y, PW, h, f"{title} ({len(data)}명)", band=band)
        rows = [[(f"{k}", {}), (f"   {v}명", {"bold": True})] for k, v in pairs]
        text(s, bx, by - 0.02, bw, 0.27 * len(pairs), rows, size=T_SMALL, line=1.25)
        y += h + 0.25
    text(s, M, y, PW, 0.9, "한 응답이 두 주제에 들 수 있습니다. 원문은 7쪽 원자료와 같은 R1~R10 번호로 엑셀에 있고, "
                           "이름이 함께 있어 이 보고서에는 넣지 않았습니다.", size=T_SMALL, line=1.25)


def p6_analysis(prs, data):
    s = page(prs, "5. 분석과 개선 계획", "이렇게 고치겠습니다", 6)            # 한 줄 제목이라 본문을 1.6"부터
    mean = {c: statistics.mean(scores(data, c)) for c in ITEMS}
    good = [f"보안 기능이 화면에서 보였습니다. 나간 글에서 가린 것 확인 {mean[9]:.1f}, 실제 값 필요 요청 막기 {mean[11]:.1f}, "
            f"유출 걱정 감소 {mean[12]:.1f}점입니다.",
            f"응답 시간 만족은 {mean[16]:.1f}점입니다.",
            f"막힌 요청을 PC 안에서 처리하는 기능(완전 로컬 모드)을 가장 원했습니다({mean[21]:.1f}점)."]
    bad = [f"처음 쓰는 사람이 할 일을 몰랐습니다(첫 화면 {mean[6]:.1f}, 각 칸 이해 {mean[7]:.1f}점). "
           "주관식에서도 5명이 적었습니다.",
           f"비용 절감 표시가 {mean[14]:.1f}점으로 가장 낮았습니다.",
           "API 문서와 코드 연동 예시가 필요하다는 의견이 있었습니다.",
           "“기린”을 이름으로 가린 사례와, 긴 요청이 잘려 보이는 문제가 1건씩 있었습니다."]
    y = 1.6
    for title, band, items, h in (("잘 된 점", GREEN, good, 2.5), ("부족한 점", RED, bad, 3.2)):  # 16pt 5줄 · 7줄
        bx, by, bw = card(s, M, y, PW, h, title, band=band)
        bullets(s, bx, by, bw, h - 0.7, items, color=band)
        y += h + 0.2
    step = 0.56                                             # 14pt 두 줄
    bx, by, bw = card(s, M, y, PW, 0.5 + 0.15 + step * len(PLANS) + 0.1, "개선 계획 (12월 경진대회 전, 아직 하지 않은 일)")
    for i, (head, body) in enumerate(PLANS, 1):
        text(s, bx, by - 0.04, 0.35, 0.4, str(i), size=T_HEAD, bold=True, color=BLUE)
        text(s, bx + 0.35, by, bw - 0.35, step, [[(f"{head}: ", {"bold": True}), (body, {})]], size=T_SMALL, line=1.2)
        by += step


def p7_raw(prs, data):
    s = page(prs, "6. 응답 원자료", "응답 원자료입니다 (이름 제외)", 7)
    cols = list(ITEMS)
    rows = [["응답"] + [str(c) for c in cols]]
    for n, r in enumerate(data, 1):
        rows.append([f"R{n}"] + [("–" if not isinstance(r[c - 1], (int, float)) else f"{r[c - 1]:.0f}") for c in cols])
    rows.append(["평균"] + [f"{statistics.mean(scores(data, c)):.1f}" for c in cols])
    first = 0.62
    w = (PW - first) / len(cols)
    text(s, M, CT - 0.15, PW, 0.6, "5점 척도 문항 (번호는 4쪽 그래프와 같습니다. “–”는 해 보지 않아 비운 선택 문항입니다)",
         size=T_SMALL, bold=True, line=1.2)
    y = table(s, M, CT + 0.5, [first] + [w] * len(cols), 0.33, rows, align=[PP_ALIGN.CENTER] * (len(cols) + 1),
              margin=0.02)
    text(s, M, y + 0.3, PW, 0.4, "응답자 정보", size=T_SMALL, bold=True)
    info = [["응답", "현재 하는 일", "생성형 AI 사용", "응답 날짜"]]
    for n, r in enumerate(data, 1):
        info.append([f"R{n}", JOB_SHORT.get(r[2], r[2]), r[3], f"{str(r[0])[5:7]}월 {str(r[0])[8:10]}일"])
    table(s, M, y + 0.75, [first, 3.3, 1.8, PW - first - 5.1], 0.32, info,
          align=[PP_ALIGN.CENTER, PP_ALIGN.LEFT, PP_ALIGN.CENTER, PP_ALIGN.CENTER])


def p8_appendix(prs, n_pages: int):
    s = page(prs, "부록", "설문지 전체 (Google Forms)", 8)
    text(s, M, CT - 0.15, PW, 1.0, f"다음 {n_pages}쪽은 실제로 배포한 설문지를 인쇄한 것입니다. "
                                   "체험판 주소와 비밀번호는 설문지 첫 화면에서 안내했습니다. 비밀번호는 가렸고, 지금은 체험판을 닫았습니다.",
         line=1.25)


def beta_password() -> str | None:
    """.env의 BETA_PASSWORD(체험판 비밀번호). 값은 출력하지 않고 PDF에서 가리는 데만 쓴다."""
    env = ROOT / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("BETA_PASSWORD="):
            return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def redact(pdf: Path, secret: str) -> int:
    """PDF에서 secret이 나오는 곳을 모두 검게 지운다(글자 자체를 없앤다). 지운 곳 수를 돌려준다.
    덧붙여 저장(incremental)하면 지우기 전 내용이 파일 안에 남아 되살릴 수 있다. 새 파일로 통째로 저장하고 바꿔 넣는다."""
    import fitz
    doc = fitz.open(pdf)
    hits = 0
    for pg in doc:
        for area in pg.search_for(secret):
            pg.add_redact_annot(area, fill=(0.07, 0.09, 0.15))
            hits += 1
        pg.apply_redactions()
    if hits:
        tmp = pdf.with_suffix(".tmp.pdf")
        doc.save(tmp, garbage=4, deflate=True)
        doc.close()
        tmp.replace(pdf)
    else:
        doc.close()
    return hits


# ── 만들기 ──

def to_pdf(pptx: Path, pdf: Path) -> None:
    """PowerPoint로 PDF를 만든다. 이 스크립트가 PowerPoint를 켰을 때만 끈다."""
    ps = (f"$running = Get-Process POWERPNT -ErrorAction SilentlyContinue; "
          f"$pp = New-Object -ComObject PowerPoint.Application; "
          f"$p = $pp.Presentations.Open('{pptx}', $true, $false, $false); $p.SaveAs('{pdf}', 32); $p.Close(); "
          f"if (-not $running) {{ $pp.Quit() }}")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


def main() -> None:
    from pypdf import PdfReader
    header, data = load()
    form_pages = len(PdfReader(FORM_PDF).pages)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(PAGE_W), Inches(PAGE_H)
    prs.core_properties.title = "가림게이트 사용자 평가 결과"
    prs.core_properties.author = "배상규"
    with tempfile.TemporaryDirectory() as tmp:
        shots = masked_shots(Path(tmp))
        p1_summary(prs, header, data)
        p2_recruit(prs, data, shots)
        p3_people(prs, header, data)
        p4_items(prs, data)
        p5_open(prs, data)
        p6_analysis(prs, data)
        p7_raw(prs, data)
        p8_appendix(prs, form_pages)
        check_text(prs)
        prs.save(OUT_PPTX)
        report = Path(tmp) / "report.pdf"
        to_pdf(OUT_PPTX, report)
        w = PdfWriter()
        w.append(str(report))
        w.append(str(FORM_PDF))
        with open(OUT_PDF, "wb") as f:
            w.write(f)
    secret = beta_password()
    hidden = redact(OUT_PDF, secret) if secret else 0
    size = OUT_PDF.stat().st_size / 1024 / 1024
    print(f"saved {OUT_PDF.name}: {len(prs.slides)}쪽 + 설문지 {form_pages}쪽, {size:.1f}MB, "
          f"비밀번호 가린 곳 {hidden}개" + ("" if secret else " (.env에 BETA_PASSWORD가 없어 건너뜀)"))


if __name__ == "__main__":
    main()
