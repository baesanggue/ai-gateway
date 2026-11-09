"""시연 화면 (#76): 왼쪽은 평범한 챗봇, 오른쪽은 게이트웨이가 그 요청에 한 일.

실행: uv run streamlit run admin/app.py   (게이트웨이와 Ollaya가 먼저 떠 있어야 한다)

- 챗봇은 OpenAI SDK로 게이트웨이를 부른다. 기존 서비스가 API 주소만 바꾸는 장면이다 (11/09 결정)
- 오른쪽 내용은 응답 헤더의 요청 ID로 게이트웨이 요청 로그를 읽어 만든다. 로그에는 가명화된 문장만 있다
- 원문(내가 친 글)은 이 화면의 세션에만 있다
- 바로 보이는 것: 흐름 한 줄, 밖으로 나간 글, 단계별 한 줄 요약. 확률·기준값·비용·시간은 펼쳐야 보인다 (11/09 사용자 의견)
"""
import html
import sys
from pathlib import Path

import streamlit as st
from openai import OpenAI, OpenAIError

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from admin.flow import build_steps, outbound_text, session_totals  # noqa: E402
from gateway.config import load_settings  # noqa: E402
from gateway.storage.log import RequestLog  # noqa: E402

EXAMPLES = [  # 시연 시나리오 3·4·5번 (CLAUDE.md 7장). 이름·번호는 가상 값
    ("배송 문의", "주문한 거 배송 언제 와요?"),
    ("사과 메일", "김민수 고객(010-1234-5678)에게 배송 지연 사과 메일 써줘"),
    ("주민번호 계산", "이 주민번호 900101-1234567로 고객 나이 계산해줘"),
]
LEVEL_KO = {"basic": "기본", "strict": "엄격", "local": "완전 로컬"}
PRESET_KO = {"shop": "쇼핑몰", "medical": "병원", "school": "학교", None: "없음"}
ICON = {"done": "✓", "skipped": "–", "blocked": "■"}
STEP_SHORT = {"mask": "가리기", "local": "로컬 판단", "cloud": "클라우드 판단", "route": "경로", "restore": "복원"}

CSS = """
<style>
:root { --gw-mono: ui-monospace, "Cascadia Mono", Consolas, monospace; }
.block-container, [data-testid="stMainBlockContainer"] { padding-top: 1.5rem; }
div[data-testid="stChatInput"] textarea { font-family: var(--gw-mono); }
div[data-testid="stChatMessage"] p { font-size: 0.98rem; }
.gw-cost { font-family: var(--gw-mono); font-size: 0.95rem; padding: 0.4rem 0; }
.gw-cost b { font-size: 1.1rem; }
.gw-flow { font-family: var(--gw-mono); font-size: 0.9rem; margin: 0.2rem 0 0.8rem 0; line-height: 1.8; }
.gw-step { padding: 0.1rem 0.45rem; border-radius: 0.3rem; white-space: nowrap; }
.gw-done { background: rgba(46, 160, 67, 0.15); }
.gw-skipped { color: #8b8b8b; }
.gw-blocked { background: rgba(218, 54, 51, 0.18); }
.gw-out { font-family: var(--gw-mono); font-size: 0.95rem; border-left: 3px solid #3b82f6; padding: 0.6rem 0.8rem;
          background: rgba(59, 130, 246, 0.08); border-radius: 0.3rem; white-space: pre-wrap; }
.gw-none { color: #8b8b8b; font-style: italic; }
</style>
"""


@st.cache_resource
def settings():
    return load_settings()


def client(s) -> OpenAI:
    g = s.config.gateway
    return OpenAI(base_url=f"http://{g.host}:{g.port}/v1", api_key="local-demo", max_retries=0, timeout=120)


def system_prompt(s) -> str | None:
    """등록한 시연용 지시문이 있으면 그것을 보낸다(#73). 없으면 보내지 않는다."""
    paths = s.config.decision.trusted_system_prompts
    if not paths:
        return None
    path = Path(paths[0]) if Path(paths[0]).is_absolute() else ROOT / paths[0]
    return path.read_text(encoding="utf-8-sig") if path.is_file() else None


def send(s, text: str) -> None:
    """메시지 하나씩 보낸다. 앞 대화까지 보내면 오른쪽 '가리기'가 앞 요청의 개인정보까지 세어 시연에서 헷갈린다
    (시연 시나리오는 모두 한 번에 끝나는 요청이다). 대화 이력 처리는 게이트웨이 시험(#37, #71)이 맡는다."""
    sp = system_prompt(s)
    messages = ([{"role": "system", "content": sp}] if sp else []) + [{"role": "user", "content": text}]
    st.session_state.chat.append({"role": "user", "content": text})
    try:
        raw = client(s).chat.completions.with_raw_response.create(model="gpt-6-luna", messages=messages)
        rid = raw.headers.get("x-gateway-request-id")
        answer = raw.parse().choices[0].message.content or ""
    except OpenAIError as e:
        st.session_state.chat.append({"role": "assistant", "content": f"(게이트웨이 오류: {type(e).__name__})",
                                      "request_id": None})
        return
    st.session_state.chat.append({"role": "assistant", "content": answer, "request_id": rid})
    st.session_state.selected = rid


def rows(s) -> dict[str, dict]:
    log = RequestLog.from_config(s.config.storage)
    ids = [m["request_id"] for m in st.session_state.chat if m.get("request_id")]
    return {i: r for i in ids if (r := log.get(i)) is not None}


def cost_line(logged: dict[str, dict]) -> str:
    cost, base = session_totals(list(logged.values()))
    saved = f" (−{1 - cost / base:.0%})" if base else ""
    return (f"<div class='gw-cost'>누적 비용 &nbsp; 기존 방식 <b>${base:.4f}</b> → 게이트웨이 <b>${cost:.4f}</b>{saved}"
            f" &nbsp;·&nbsp; 요청 {len(logged)}건</div>")


def flow_line(steps) -> str:
    parts = [f"<span class='gw-step gw-{s.status}'>{ICON[s.status]} {STEP_SHORT[s.key]}</span>" for s in steps]
    return "<div class='gw-flow'>" + " → ".join(parts) + "</div>"


def gateway_panel(s, row: dict | None) -> None:
    st.markdown("#### 게이트웨이가 한 일")
    if row is None:
        st.caption("왼쪽에서 메시지를 보내면, 그 요청을 게이트웨이가 어떻게 처리했는지 여기에 나옵니다.")
        return
    steps = build_steps(row, s.config.decision.thresholds)
    st.markdown(flow_line(steps), unsafe_allow_html=True)
    out = outbound_text(row)
    st.caption("밖(외부 AI)으로 나간 글")
    if out is None:
        st.markdown("<div class='gw-out gw-none'>나간 것 없음</div>", unsafe_allow_html=True)
    else:
        st.markdown(f"<div class='gw-out'>{html.escape(out)}</div>", unsafe_allow_html=True)
    st.write("")
    for step in steps:
        with st.expander(f"{ICON[step.status]}  **{step.title}** — {step.summary}", expanded=False):
            if not step.details:
                st.caption("더 볼 내용이 없습니다.")
            for label, value in step.details:
                st.markdown(f"**{label}** &nbsp; {html.escape(str(value))}", unsafe_allow_html=True)


def main() -> None:
    st.set_page_config(page_title="AI 게이트웨이 시연", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    s = settings()
    st.session_state.setdefault("chat", [])
    st.session_state.setdefault("selected", None)
    logged = rows(s)

    top_left, top_right = st.columns([3, 1])
    top_left.markdown(cost_line(logged), unsafe_allow_html=True)
    top_right.markdown(f"<div class='gw-cost' style='text-align:right'>보안 {LEVEL_KO.get(s.config.security_level)}"
                       f" · 업종 {PRESET_KO.get(s.config.preset, s.config.preset)}</div>", unsafe_allow_html=True)

    chat_col, gw_col = st.columns([1, 1], gap="large")
    with chat_col:
        st.markdown("#### 💬 챗봇")
        box = st.container(height=440)
        for m in st.session_state.chat:
            with box.chat_message("user" if m["role"] == "user" else "assistant"):
                # 물결표 두 개 사이를 마크다운이 취소선으로 바꾼다("1~3일 … 1~2일"). 답변의 마크다운은 살리고 물결표만 그대로
                st.markdown(m["content"].replace("~", "\\~"))
                if m.get("request_id") and st.button("이 요청 보기", key=f"sel-{m['request_id']}", type="tertiary"):
                    st.session_state.selected = m["request_id"]
                    st.rerun()
        buttons = st.columns(len(EXAMPLES))
        for col, (label, text) in zip(buttons, EXAMPLES):
            if col.button(label, width="stretch"):
                send(s, text)
                st.rerun()
        if text := st.chat_input("› 메시지를 입력하세요"):
            send(s, text)
            st.rerun()
    with gw_col:
        gateway_panel(s, logged.get(st.session_state.selected))


main()
