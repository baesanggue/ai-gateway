"""관리·시연 화면에 공통인 것: 색과 글꼴(CSS), 설정, 게이트웨이 주소, 왼쪽 메뉴의 상태 표시 (#76).

디자인은 11/09 사용자 의견을 따른다: 예전 화면 예시(docs/design/ui-demo.html)의 모양(회색 메뉴, 이름표가 붙은 카드,
색으로 나눈 칸), 이모지 없음, 메뉴는 왼쪽에서 열고 닫는다, 창은 1200×800 안팎에 맞춘다.
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

CSS = """
<style>
:root {
  --gw-mono: "Cascadia Mono", Consolas, "D2Coding", monospace;
  --gw-line: #e5e7eb; --gw-sub: #6b7280; --gw-soft: #fafafa;
}
/* 본문 글꼴만 바꾼다. 아이콘(Material Symbols) 글꼴까지 덮으면 메뉴 아이콘이 글자로 나온다 */
.stMarkdown, .stMarkdown p, .stCaption, .gw-term, .gw-panel, .gw-step, .gw-metric, .gw-status, .gw-box, .gw-kv { font-family: "Malgun Gothic", "Apple SD Gothic Neo", sans-serif; }
.gw-term, .gw-metric .v { font-family: var(--gw-mono) !important; }
.block-container, [data-testid="stMainBlockContainer"] { padding-top: 1.2rem; padding-bottom: 1rem; max-width: 100%; }
h1.gw-title { font-size: 1.35rem; margin: 0 0 0.1rem 0; padding: 0; }
.gw-note { font-size: 0.8rem; color: var(--gw-sub); margin-bottom: 0.6rem; }
.gw-label { font-size: 0.78rem; color: var(--gw-sub); font-weight: 600; margin: 0.2rem 0 0.35rem 0; }

/* 숫자 카드 */
.gw-metrics { display: flex; gap: 10px; margin: 0.3rem 0 0.8rem 0; }
.gw-metric { flex: 1; border: 1px solid var(--gw-line); border-radius: 10px; padding: 6px 14px; background: #fff; }
.gw-metric .k { font-size: 0.75rem; color: var(--gw-sub); }
.gw-metric .v { font-size: 1.2rem; font-weight: 700; margin-top: 2px; font-family: var(--gw-mono); }
.gw-metric .d { font-size: 0.72rem; color: var(--gw-sub); }
.gw-metric.save { background: #ecfdf5; border-color: #a7f3d0; }
.gw-metric.save .v { color: #047857; }

/* 터미널(챗봇) */
.gw-term { background: #1e1f24; color: #e5e7eb; font-family: var(--gw-mono); font-size: 0.82rem;
  border-radius: 8px 8px 0 0; padding: 10px 12px; height: 230px; overflow-y: auto; display: flex; flex-direction: column-reverse; }
.gw-term .u { color: #93c5fd; }
.gw-term .b { color: #e5e7eb; white-space: pre-wrap; margin: 2px 0 10px 14px; }
.gw-term .blk { color: #fca5a5; }
.gw-term .meta { color: #6b7280; }
.gw-term .empty { color: #6b7280; }
div[data-testid="InputInstructions"] { display: none; }
/* 입력 줄(시연 화면의 cli_box만): 터미널 바로 아래에 붙여, 어디에 쓰는지·커서가 어디 있는지 보이게 (11/09 사용자 의견) */
.st-key-cli_box div[data-testid="stForm"] { border: none; padding: 4px 8px 8px 4px; background: #1e1f24; border-radius: 0 0 8px 8px; }
.st-key-cli_box div[data-testid="stForm"] div[data-baseweb="input"] { background: #2b2d35; border: 1px solid #4b5563; border-radius: 6px; }
.st-key-cli_box div[data-testid="stForm"] div[data-baseweb="input"]:focus-within { border-color: #60a5fa; box-shadow: 0 0 0 2px rgba(96,165,250,0.35); }
.st-key-cli_box div[data-testid="stForm"] [data-baseweb="base-input"], .st-key-cli_box div[data-testid="stForm"] input {
  font-family: var(--gw-mono); background: #2b2d35 !important; color: #f9fafb !important; caret-color: #93c5fd; }
.st-key-cli_box div[data-testid="stForm"] input::placeholder { color: #9ca3af; opacity: 1; }
.gw-prompt { font-family: var(--gw-mono); color: #93c5fd; font-size: 1.3rem; font-weight: 700; text-align: center; }
.gw-hint { font-size: 0.82rem; color: var(--gw-sub); border: 1px dashed var(--gw-line); border-radius: 8px; padding: 14px; }

/* 처리 과정 */
.st-key-steps, .st-key-log_steps { overflow-x: hidden; }
/* 체험판 안내 (#90): 처리 과정 칸·작은 창에 다 들어가게 작은 글씨 */
.st-key-beta_guide p, .st-key-beta_guide li, div[data-testid="stPopoverBody"] p, div[data-testid="stPopoverBody"] li {
  font-size: 0.8rem; line-height: 1.5; margin-bottom: 0.25rem; }
.st-key-beta_guide code, div[data-testid="stPopoverBody"] code { font-size: 0.76rem; }
.gw-step { border: 1px solid var(--gw-line); border-radius: 8px; padding: 5px 10px; background: var(--gw-soft); }
.gw-step .h { display: flex; justify-content: space-between; align-items: center; font-size: 0.8rem; font-weight: 700; }
.gw-step .s { font-size: 0.82rem; margin-top: 2px; }
.gw-step.skipped { opacity: 0.55; }
.gw-step.blocked { border-color: #fecaca; background: #fef2f2; }
.gw-chip { display: inline-block; padding: 0 8px; border-radius: 10px; font-size: 0.72rem; font-weight: 600; }
.gw-chip.done { background: #dcfce7; color: #166534; }
.gw-chip.skipped { background: #f3f4f6; color: #6b7280; }
.gw-chip.blocked { background: #fee2e2; color: #991b1b; }

/* 나간 글 / 받은 답변 */
.gw-panel { border-radius: 10px; padding: 8px 14px; line-height: 1.55; font-size: 0.84rem; height: 118px; overflow-y: auto; margin-top: 0.5rem; }
.gw-panel h4 { margin: 0 0 6px 0; font-size: 0.82rem; }
.gw-panel.out { background: #fff7ed; border: 1px solid #fed7aa; }
.gw-panel.in { background: #f0fdf4; border: 1px solid #bbf7d0; }
.gw-panel .none { color: var(--gw-sub); }
.gw-tok { background: #fde68a; padding: 0 4px; border-radius: 4px; font-weight: 700; }

/* 왼쪽 메뉴의 상태 */
.gw-status { font-size: 0.8rem; line-height: 1.9; }
.gw-status .ok { color: #16a34a; } .gw-status .bad { color: #dc2626; }

/* 이름표 + 값 (설정·현황) */
.gw-kv { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; font-size: 0.85rem; margin: 0.2rem 0 0.5rem 0; }
.gw-kv span { color: var(--gw-sub); }

/* 가로 막대 (현황): 한 계열 한 색, 값은 글자색으로 옆에 */
.gw-bars { display: flex; flex-direction: column; gap: 7px; font-size: 0.82rem; }
.gw-bar { display: grid; grid-template-columns: 1fr auto; align-items: baseline; row-gap: 3px; column-gap: 8px; }
.gw-bar .l { color: #374151; }
.gw-bar .t { grid-column: 1 / -1; height: 8px; }
.gw-bar .f { display: block; height: 100%; background: #2563eb; border-radius: 0 4px 4px 0; }
.gw-bar .v { font-family: var(--gw-mono); color: #111827; }
.gw-bar .v i { font-style: normal; color: var(--gw-sub); margin-left: 4px; }
.gw-box { border: 1px solid var(--gw-line); border-radius: 10px; padding: 10px 14px; background: #fff; height: 100%; }
.gw-box h4 { font-size: 0.8rem; color: var(--gw-sub); margin: 0 0 8px 0; font-weight: 600; }
.gw-box .empty { font-size: 0.82rem; color: var(--gw-sub); }
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


def steps_view(row: dict, thresholds: dict, height: int, key: str) -> None:
    """처리 과정 다섯 단계 카드. 이 칸 안에서만 스크롤하고, 자세히는 정보 아이콘의 작은 창으로 띄운다 (11/09 의견)."""
    from admin.flow import build_steps
    with st.container(height=height, border=False, key=key):
        for n, step in enumerate(build_steps(row, thresholds), 1):
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
