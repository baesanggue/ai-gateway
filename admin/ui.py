"""관리·시연 화면에 공통인 것: 색과 글꼴(CSS), 설정, 게이트웨이 주소, 왼쪽 메뉴의 상태 표시 (#76).

디자인은 11/09 사용자 의견을 따른다: 예전 화면 예시(docs/design/ui-demo.html)의 모양(회색 메뉴, 이름표가 붙은 카드,
색으로 나눈 칸), 이모지 없음, 메뉴는 왼쪽에서 열고 닫는다, 창은 1200×800 안팎에 맞춘다.
11/19 (#131): 발표 자료와 같은 읽기 쉬움 규칙 — 검정·진한 글씨, 색 머리 띠 상자, 14px 이상 (CSS 맨 위 설명).
"""
import html
import sys
from pathlib import Path

import httpx
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from gateway.config import load_settings  # noqa: E402

LEVEL_KO = {"basic": "기본", "strict": "엄격", "local": "완전 로컬"}
PRESET_KO = {"shop": "쇼핑몰", "medical": "병원", "school": "학교", None: "없음"}
NUMS = "①②③④"            # 시연 예시 단추·첫 화면 안내의 번호 (#131). 예시는 넷까지 (DisplayConfig.examples)

CSS = """
<style>
/* 읽기 쉬움 규칙 (#131, 11/19 사용자 선택: 발표 자료와 같은 규칙, 11/15 D30)
   - 글씨는 검정(--gw-ink)·진한 회색(--gw-dark)만. 연한 회색 글씨는 쓰지 않는다
   - 칸은 회색 가는 선 대신 색 머리 띠가 있는 상자로 나눈다 (파랑 기본, 초록 절감·복원, 주황 외부로 나간 것, 빨강 차단)
   - 글씨는 14px(0.875rem)보다 작게 하지 않는다. 역할마다 크기 하나: 제목 1.35rem, 머리 띠·단추 0.95rem, 본문 0.875rem */
:root {
  --gw-mono: "Cascadia Mono", Consolas, "D2Coding", monospace;
  --gw-ink: #111827; --gw-dark: #1f2937; --gw-navy: #0f1e3d; --gw-band: #1e40af; --gw-tint: #dbeafe;
  --gw-green: #0f7b5a; --gw-gtint: #d1fae5; --gw-red: #c0392b; --gw-rtint: #fde2e1; --gw-amber: #b45309; --gw-atint: #fff7ed;
  --gw-small: 0.875rem; --gw-head: 0.95rem;
}
/* 본문 글꼴만 바꾼다. 아이콘(Material Symbols) 글꼴까지 덮으면 메뉴 아이콘이 글자로 나온다 */
.stMarkdown, .stMarkdown p, .stCaption, .gw-term, .gw-panel, .gw-step, .gw-metric, .gw-status, .gw-box, .gw-kv { font-family: "Malgun Gothic", "Apple SD Gothic Neo", sans-serif; }
.gw-term, .gw-metric .v { font-family: var(--gw-mono) !important; }
.block-container, [data-testid="stMainBlockContainer"] { padding-top: 1.2rem; padding-bottom: 1rem; max-width: 100%; }
h1.gw-title { font-size: 1.35rem; margin: 0 0 0.1rem 0; padding: 0; color: var(--gw-ink); }
.gw-note { font-size: var(--gw-small); color: var(--gw-dark); margin-bottom: 0.6rem; }
/* 칸 이름: 파란 바탕 흰 글씨 이름표 (발표 자료의 label_row) */
.gw-label { display: inline-block; font-size: var(--gw-small); color: #fff; background: var(--gw-band); font-weight: 700;
  padding: 1px 10px; border-radius: 4px; margin: 0.2rem 0 0.4rem 0; }
/* Streamlit 캡션은 글씨 색에 opacity 0.6까지 겹쳐 연한 회색이 된다 → 진한 회색, 불투명 */
[data-testid="stCaptionContainer"], .stCaption, [data-testid="stCaptionContainer"] p { color: var(--gw-dark) !important;
  opacity: 1 !important; font-size: var(--gw-small); }

/* 숫자 카드: 파란 머리 띠(이름) + 값 + 설명 */
.gw-metrics { display: flex; gap: 10px; margin: 0.3rem 0 0.7rem 0; }
.gw-metric { flex: 1; border: 2px solid var(--gw-band); border-radius: 8px; overflow: hidden; background: #fff; }
.gw-metric .k { font-size: var(--gw-small); color: #fff; background: var(--gw-band); font-weight: 700; padding: 2px 12px; }
.gw-metric .v { font-size: 1.2rem; font-weight: 700; padding: 3px 12px 0; font-family: var(--gw-mono); color: var(--gw-ink); }
.gw-metric .d { font-size: var(--gw-small); color: var(--gw-dark); padding: 0 12px 5px; }
.gw-metric.save { border-color: var(--gw-green); }
.gw-metric.save .k { background: var(--gw-green); }
.gw-metric.save .v { color: var(--gw-green); }
/* 비용을 한 문장으로 (#132). 진한 글씨와 초록 띠 */
.gw-saving { margin: -0.35rem 0 0.5rem 0; padding: 7px 12px; border-left: 4px solid var(--gw-green); background: var(--gw-gtint);
  border-radius: 6px; font-size: 0.9rem; color: var(--gw-ink); font-weight: 600; }
.gw-saving span { font-size: var(--gw-small); font-weight: 400; color: var(--gw-dark); margin-left: 0.4rem; }

/* 터미널(챗봇) */
.gw-term { background: #1e1f24; color: #e5e7eb; font-family: var(--gw-mono); font-size: var(--gw-small);
  border-radius: 8px 8px 0 0; padding: 10px 12px; height: 230px; overflow-y: auto; display: flex; flex-direction: column-reverse; }
.gw-term .u { color: #93c5fd; }
.gw-term .b { color: #e5e7eb; white-space: pre-wrap; margin: 2px 0 10px 14px; }
.gw-term .blk { color: #fca5a5; }
.gw-term .meta { color: #cbd5e1; }
.gw-term .empty { color: #cbd5e1; }
div[data-testid="InputInstructions"] { display: none; }
/* 입력 줄(시연 화면의 cli_box만): 터미널 바로 아래에 붙여, 어디에 쓰는지·커서가 어디 있는지 보이게 (11/09 사용자 의견) */
.st-key-cli_box div[data-testid="stForm"] { border: none; padding: 4px 8px 8px 4px; background: #1e1f24; border-radius: 0 0 8px 8px; }
.st-key-cli_box div[data-testid="stForm"] div[data-baseweb="input"] { background: #2b2d35; border: 1px solid #4b5563; border-radius: 6px; }
.st-key-cli_box div[data-testid="stForm"] div[data-baseweb="input"]:focus-within { border-color: #60a5fa; box-shadow: 0 0 0 2px rgba(96,165,250,0.35); }
.st-key-cli_box div[data-testid="stForm"] [data-baseweb="base-input"], .st-key-cli_box div[data-testid="stForm"] input {
  font-family: var(--gw-mono); background: #2b2d35 !important; color: #f9fafb !important; caret-color: #93c5fd; }
.st-key-cli_box div[data-testid="stForm"] input::placeholder { color: #9ca3af; opacity: 1; }
.gw-prompt { font-family: var(--gw-mono); color: #93c5fd; font-size: 1.3rem; font-weight: 700; text-align: center; }
/* 번호 붙은 예시 단추 (#131): 상자 전체가 단추. 단추의 ::after를 상자 크기로 늘려 설명 글을 눌러도 눌린다 */
[class*="st-key-exbox_"] { position: relative; border: 2px solid #1e40af; border-radius: 8px; padding: 5px 10px 6px;
  background: #fff; gap: 0; }
[class*="st-key-exbox_"] [data-testid="stElementContainer"], [class*="st-key-exbox_"] .stButton,
[class*="st-key-exbox_"] [data-testid="stTooltipHoverTarget"], [class*="st-key-exbox_"] button { position: static; }
[class*="st-key-exbox_"] button { border: none; background: transparent; padding: 0; min-height: 0; width: 100%;
  justify-content: flex-start; box-shadow: none; }
[class*="st-key-exbox_"] button::after { content: ""; position: absolute; inset: 0; border-radius: 8px; }
[class*="st-key-exbox_"] button p { font-size: 0.95rem; font-weight: 700; color: #111827; }
[class*="st-key-exbox_"]:hover { background: #dbeafe; }
.gw-ex-desc { font-size: 0.875rem; color: #1f2937; line-height: 1.35; }
[class*="st-key-exbox_"] [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
[class*="st-key-exbox_done_"] { border-color: #0f7b5a; background: #d1fae5; }
[class*="st-key-exbox_done_"] button p { color: #065f46; }
[class*="st-key-exbox_next_"] { border-width: 3px; box-shadow: 0 0 0 3px #bfdbfe; }
.gw-hint { font-size: var(--gw-small); color: var(--gw-ink); background: var(--gw-tint); border-radius: 8px; padding: 14px; }

/* 첫 화면 안내 (#131): 남색 머리 띠 상자 안에 세 단계 */
.st-key-guide { border: 2px solid #0f1e3d; border-radius: 8px; overflow: hidden; gap: 0.5rem; padding-bottom: 10px;
  margin-bottom: 0.6rem; }
.gw-guide-hd { background: #0f1e3d; color: #fff; font-weight: 700; font-size: 0.95rem; padding: 6px 14px; }
.gw-gsteps { display: flex; gap: 10px; }
.st-key-guide .gw-gsteps { padding: 10px 12px 0; }
.gw-gstep { flex: 1; background: #dbeafe; border-radius: 8px; padding: 9px 12px; }
.gw-gstep:nth-child(2) { flex: 1.35; }
.gw-gstep .t { font-weight: 700; font-size: 0.95rem; color: #111827; }
.gw-gstep .n { display: inline-block; width: 24px; height: 24px; line-height: 24px; border-radius: 50%; background: #1e40af;
  color: #fff; text-align: center; font-size: 0.875rem; margin-right: 6px; }
.gw-gstep .s { font-size: 0.875rem; color: #111827; margin-top: 5px; line-height: 1.5; }
.st-key-guide [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
div[data-testid="stPopoverBody"] .gw-gsteps { flex-direction: column; width: 420px; margin-bottom: 1.3rem; }
.st-key-guide [data-testid="stHorizontalBlock"] { padding: 0 12px; }
.st-key-guide [data-testid="stPageLink"] a { background: #1d4ed8; border-radius: 6px; padding: 4px 12px; }
.st-key-guide [data-testid="stPageLink"] a p, .st-key-guide [data-testid="stPageLink"] a span { color: #fff !important; font-weight: 700; }
.st-key-guide button { border: 2px solid #1f2937; }
.st-key-guide button p { color: #111827; font-weight: 700; }

/* 처리 과정 */
.st-key-steps, .st-key-log_steps { overflow-x: hidden; }
/* 체험판 안내 (#90): 처리 과정 칸·작은 창에 다 들어가게 작은 글씨 */
.st-key-beta_guide p, .st-key-beta_guide li, div[data-testid="stPopoverBody"] p, div[data-testid="stPopoverBody"] li {
  font-size: var(--gw-small); line-height: 1.5; margin-bottom: 0.25rem; color: var(--gw-ink); }
.st-key-beta_guide code, div[data-testid="stPopoverBody"] code { font-size: var(--gw-small); }
/* 단계 카드: 한 일은 파란 테두리, 건너뛴 단계는 연한 회색 바탕(글씨는 그대로 진하게), 막은 단계는 빨강 */
.gw-step { border: 2px solid var(--gw-band); border-radius: 8px; padding: 4px 10px; background: #fff; color: var(--gw-ink); }
.gw-step .h { display: flex; justify-content: space-between; align-items: center; font-size: var(--gw-small); font-weight: 700; }
.gw-step .s { font-size: var(--gw-small); margin-top: 2px; }
.gw-step.skipped { border-color: #9ca3af; background: #f3f4f6; }
.gw-step.blocked { border-color: var(--gw-red); background: var(--gw-rtint); }
.gw-chip { display: inline-block; padding: 0 8px; border-radius: 10px; font-size: var(--gw-small); font-weight: 700; }
.gw-chip.done { background: var(--gw-green); color: #fff; }
.gw-chip.skipped { background: #4b5563; color: #fff; }
.gw-chip.blocked { background: var(--gw-red); color: #fff; }

/* 나간 글(주황 띠) / 받은 답변(초록 띠). 머리 띠는 칸 안에서 스크롤해도 위에 붙어 있다 */
.gw-panel { border-radius: 8px; padding: 0 12px 8px; line-height: 1.55; font-size: var(--gw-small); height: 118px; overflow-y: auto;
  margin-top: 0.5rem; color: var(--gw-ink); background: #fff; }
.gw-panel .hd { position: sticky; top: 0; margin: 0 -12px 6px; padding: 2px 12px; font-size: var(--gw-small); font-weight: 700; color: #fff; }
.gw-panel.out { border: 2px solid var(--gw-amber); background: var(--gw-atint); }
.gw-panel.out .hd { background: var(--gw-amber); }
.gw-panel.in { border: 2px solid var(--gw-green); background: #f0fdf4; }
.gw-panel.in .hd { background: var(--gw-green); }
.gw-panel .none { color: var(--gw-dark); }
.gw-tok { background: #fde68a; padding: 0 4px; border-radius: 4px; font-weight: 700; }

/* 왼쪽 메뉴의 상태 */
.gw-status { font-size: var(--gw-small); line-height: 1.9; color: var(--gw-ink); }
.gw-status .ok { color: var(--gw-green); } .gw-status .bad { color: var(--gw-red); }

/* 이름표 + 값 (설정·현황) */
.gw-kv { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; font-size: var(--gw-small); margin: 0.2rem 0 0.5rem 0;
  color: var(--gw-ink); }
.gw-kv span { color: var(--gw-dark); }

/* 가로 막대 (현황): 한 계열 한 색, 값은 글자색으로 옆에 */
.gw-bars { display: flex; flex-direction: column; gap: 7px; font-size: var(--gw-small); }
.gw-bar { display: grid; grid-template-columns: 1fr auto; align-items: baseline; row-gap: 3px; column-gap: 8px; }
.gw-bar .l { color: var(--gw-ink); }
.gw-bar .t { grid-column: 1 / -1; height: 8px; }
.gw-bar .f { display: block; height: 100%; background: var(--gw-band); border-radius: 0 4px 4px 0; }
.gw-bar .v { font-family: var(--gw-mono); color: var(--gw-ink); }
.gw-bar .v i { font-style: normal; color: var(--gw-dark); margin-left: 4px; }
/* 상자(현황): 파란 머리 띠 + 내용 */
.gw-box { border: 2px solid var(--gw-band); border-radius: 8px; overflow: hidden; background: #fff; height: 100%; }
.gw-box .hd { font-size: var(--gw-small); color: #fff; background: var(--gw-band); padding: 3px 12px; font-weight: 700; }
.gw-box .bd { padding: 8px 12px 10px; }
.gw-box .empty { font-size: var(--gw-small); color: var(--gw-ink); }
</style>
"""


@st.cache_resource
def settings():
    return load_settings()


def gateway_url(s) -> str:
    g = s.config.gateway
    return f"http://{g.host}:{g.port}"


@st.cache_data(ttl=5, show_spinner=False)
def health(url: str) -> dict | None:
    """게이트웨이 /healthz. 응답이 없으면 None (5초마다 다시 본다)."""
    try:
        r = httpx.get(f"{url}/healthz", timeout=2)
        return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


def bars(items: list[tuple[str, int]], unit: str = "건") -> str:
    """가로 막대 (한 계열이라 한 색, 범례 없음). 값과 비율은 막대 옆 글자로, 마우스를 올리면 같은 내용이 뜬다."""
    total = sum(n for _, n in items) or 1
    top = max((n for _, n in items), default=0) or 1
    rows = "".join(
        f"<div class='gw-bar' title='{html.escape(label)}: {n}{unit} ({n / total:.0%})'>"
        f"<span class='l'>{html.escape(label)}</span><span class='v'>{n}{unit} <i>{n / total:.0%}</i></span>"
        f"<span class='t'><span class='f' style='width:{max(n / top * 100, 1.5 if n else 0):.1f}%'></span></span></div>"
        for label, n in items)
    return f"<div class='gw-bars'>{rows}</div>"


STATUS_KO = {"done": "완료", "skipped": "건너뜀", "blocked": "차단"}


def step_card(n: int, step) -> str:
    return (f"<div class='gw-step {step.status}'><div class='h'><span>{n}. {step.title}</span>"
            f"<span class='gw-chip {step.status}'>{STATUS_KO[step.status]}</span></div>"
            f"<div class='s'>{html.escape(step.summary)}</div></div>")


def steps_view(row: dict, thresholds: dict, height: int, key: str, rate: float | None = None) -> None:
    """처리 과정 다섯 단계 카드. 이 칸 안에서만 스크롤하고, 자세히는 정보 아이콘의 작은 창으로 띄운다 (11/09 의견).
    rate: 비용을 원화로 보여 줄 환율 (#132, display.krw_per_usd)."""
    from admin.flow import build_steps
    with st.container(height=height, border=False, key=key):
        for n, step in enumerate(build_steps(row, thresholds, rate=rate), 1):
            card, more = st.columns([6, 0.7], vertical_alignment="center", gap="small")
            card.markdown(step_card(n, step), unsafe_allow_html=True)
            if step.details:
                with more.popover("", icon=":material/info:", help="자세히: 확률·기준값·비용·시간"):
                    st.markdown(f"**{n}. {step.title}**")
                    st.table({"항목": [k for k, _ in step.details], "값": [str(v) for _, v in step.details]},
                             hide_index=True)


def page_header(title: str, note: str) -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(f"<h1 class='gw-title'>{title}</h1><div class='gw-note'>{note}</div>", unsafe_allow_html=True)


def sidebar_status(s) -> None:
    """왼쪽 메뉴 아래쪽: 게이트웨이가 켜져 있는지, 보안 수준, 업종, 사전.
    게이트웨이가 켜져 있으면 게이트웨이가 지금 쓰는 값을, 꺼져 있으면 설정 파일의 값을 보인다."""
    h = health(gateway_url(s))
    dot = "<span class='ok'>●</span> 연결됨" if h else "<span class='bad'>●</span> 꺼져 있음"
    level = (h or {}).get("security_level", s.config.security_level)
    preset = (h or {}).get("preset", s.config.preset)
    dictionary = {"example": "시연용", "custom": "회사 사전"}.get((h or {}).get("dictionary"), "-")
    with st.sidebar:
        st.markdown("<div class='gw-label'>상태</div>", unsafe_allow_html=True)
        st.markdown(
            f"<div class='gw-status'>게이트웨이 &nbsp;{dot}<br>"
            f"보안 수준 &nbsp;<b>{LEVEL_KO.get(level, level)}</b><br>"
            f"업종 &nbsp;<b>{PRESET_KO.get(preset, preset)}</b><br>"
            f"사용자 사전 &nbsp;<b>{dictionary}</b></div>", unsafe_allow_html=True)
        from admin import beta
        st.caption("체험판 · 설정은 운영자만 바꿉니다." if beta.enabled(s) else "설정 메뉴에서 바꾸면 바로 적용됩니다.")
