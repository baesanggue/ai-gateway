"""시연 화면 (#76): 기존 챗봇(터미널 모양)의 API 주소만 게이트웨이로 바꾼 상태에서, 게이트웨이가 요청마다 한 일을 보여 준다.

- 챗봇은 OpenAI SDK로 게이트웨이를 부른다. 오른쪽·아래 내용은 응답 헤더의 요청 ID로 요청 로그를 읽어 만든다
- 로그에는 가명화된 문장만 있다. 원문(입력한 글)과 받은 답변은 이 화면의 세션에만 있다
- 메시지는 하나씩 보낸다 (앞 대화의 개인정보까지 세면 시연에서 헷갈린다)
"""
import html
import re
from pathlib import Path

import streamlit as st
from openai import OpenAI, OpenAIError

from admin.flow import build_steps, outbound_text, session_totals
from admin.ui import ROOT, gateway_url, page_header, settings
from gateway.storage.log import RequestLog

TOKEN = re.compile(r"\[[^\[\]\s]{1,12}\d+\]")
STATUS_KO = {"done": "완료", "skipped": "건너뜀", "blocked": "차단"}


def _client(s) -> OpenAI:
    return OpenAI(base_url=f"{gateway_url(s)}/v1", api_key="local-demo", max_retries=0, timeout=120)


def _system_prompt(s) -> str | None:
    """등록한 시연용 지시문(#73)이 있으면 함께 보낸다."""
    paths = s.config.decision.trusted_system_prompts
    if not paths:
        return None
    path = Path(paths[0]) if Path(paths[0]).is_absolute() else ROOT / paths[0]
    return path.read_text(encoding="utf-8-sig") if path.is_file() else None


def _send(s, text: str) -> None:
    sp = _system_prompt(s)
    messages = ([{"role": "system", "content": sp}] if sp else []) + [{"role": "user", "content": text}]
    turn = {"user": text, "answer": "", "request_id": None, "route": None}
    try:
        raw = _client(s).chat.completions.with_raw_response.create(model="gpt-6-luna", messages=messages)
        turn["request_id"] = raw.headers.get("x-gateway-request-id")
        choice = raw.parse().choices[0]
        turn["answer"] = choice.message.content or ""
        turn["route"] = "block" if choice.finish_reason == "content_filter" else "ok"
    except OpenAIError as e:
        turn["answer"] = f"게이트웨이에 연결하지 못했습니다 ({type(e).__name__})"
        turn["route"] = "error"
    st.session_state.turns.append(turn)
    st.session_state.selected = len(st.session_state.turns) - 1


def _metrics(logged: list[dict]) -> str:
    cost, base = session_totals(logged)
    saved = f"{1 - cost / base:.0%}" if base else "-"
    return ("<div class='gw-metrics'>"
            f"<div class='gw-metric'><div class='k'>기존 방식 (전부 고성능 모델)</div><div class='v'>${base:.4f}</div>"
            f"<div class='d'>요청 {len(logged)}건 누적</div></div>"
            f"<div class='gw-metric'><div class='k'>게이트웨이 사용</div><div class='v'>${cost:.4f}</div>"
            "<div class='d'>템플릿·저렴한 모델·차단 포함</div></div>"
            f"<div class='gw-metric save'><div class='k'>절감</div><div class='v'>{saved}</div>"
            f"<div class='d'>${max(base - cost, 0):.4f} 아낌</div></div></div>")


def _terminal(turns: list[dict]) -> str:
    if not turns:
        body = "<div class='empty'>메시지를 입력하면 이 챗봇이 게이트웨이를 거쳐 답합니다.</div>"
    else:
        lines = []
        for i, t in enumerate(turns, 1):
            cls = "b blk" if t["route"] in ("block", "error") else "b"
            lines.append(f"<div><span class='meta'>[{i}]</span> <span class='u'>&gt; {html.escape(t['user'])}</span></div>"
                         f"<div class='{cls}'>{html.escape(t['answer'])}</div>")
        body = "".join(lines)
    return f"<div class='gw-term'><div>{body}</div></div>"     # column-reverse 안쪽 한 덩어리 → 맨 아래부터 보인다


def _steps(steps) -> str:
    out = []
    for n, s in enumerate(steps, 1):
        out.append(f"<div class='gw-step {s.status}'><div class='h'><span>{n}. {s.title}</span>"
                   f"<span class='gw-chip {s.status}'>{STATUS_KO[s.status]}</span></div>"
                   f"<div class='s'>{html.escape(s.summary)}</div></div>")
    return "".join(out)


def _panels(row: dict | None, turn: dict) -> str:
    out = outbound_text(row) if row else None
    if out is None:
        out_html = "<span class='none'>외부로 나간 것 없음</span>"
    else:
        out_html = TOKEN.sub(lambda m: f"<span class='gw-tok'>{m.group()}</span>", html.escape(out))
    answer = html.escape(turn["answer"]) or "<span class='none'>-</span>"
    return ("<div style='display:flex;gap:10px'>"
            f"<div class='gw-panel out' style='flex:1'><h4>외부 AI 서버로 나간 텍스트</h4>{out_html}</div>"
            f"<div class='gw-panel in' style='flex:1'><h4>사용자가 받은 답변 (이 PC에서 복원)</h4>"
            f"<div style='white-space:pre-wrap'>{answer}</div></div></div>")


def page() -> None:
    s = settings()
    page_header("시연", f"기존 챗봇의 API 주소만 게이트웨이(<code>{gateway_url(s)}/v1</code>)로 바꾼 상태입니다. "
                       "개인정보는 가려서 보내고, 요청마다 필요한 만큼의 모델에만 보냅니다.")
    st.session_state.setdefault("turns", [])
    st.session_state.setdefault("selected", None)
    turns = st.session_state.turns
    log = RequestLog.from_config(s.config.storage)
    rows = [log.get(t["request_id"]) if t["request_id"] else None for t in turns]

    st.markdown(_metrics([r for r in rows if r]), unsafe_allow_html=True)

    left, right = st.columns([1.15, 1], gap="medium")
    with left:
        st.markdown("<div class='gw-label'>챗봇 (기존 서비스)</div>", unsafe_allow_html=True)
        st.markdown(_terminal(turns), unsafe_allow_html=True)
        with st.form("cli", clear_on_submit=True, border=False):
            c1, c2 = st.columns([4, 1.1], vertical_alignment="center")
            text = c1.text_input("메시지", placeholder="> 메시지를 입력하고 Enter", label_visibility="collapsed")
            sent = c2.form_submit_button("보내기", width="stretch")
        if sent and text.strip():
            with st.spinner("게이트웨이가 처리하는 중"):
                _send(s, text.strip())
            st.rerun()
    with right:
        st.markdown("<div class='gw-label'>게이트웨이 처리 과정</div>", unsafe_allow_html=True)
        if not turns:
            st.caption("요청을 보내면 단계별로 무엇을 했는지 여기에 나옵니다.")
        else:
            labels = [f"[{i}] {t['user'][:28]}" for i, t in enumerate(turns, 1)]
            sel = st.selectbox("요청", range(len(turns)), index=st.session_state.selected,
                               format_func=lambda i: labels[i], label_visibility="collapsed")
            st.session_state.selected = sel
            row = rows[sel]
            if row is None:
                st.warning("이 요청의 기록을 찾지 못했습니다 (게이트웨이 연결 실패).")
            else:
                steps = build_steps(row, s.config.decision.thresholds)
                st.markdown(_steps(steps), unsafe_allow_html=True)
                if st.toggle("자세히 (확률·기준값·비용·시간)", key=f"detail-{sel}"):
                    for n, step in enumerate(steps, 1):
                        if step.details:
                            st.markdown(f"**{n}. {step.title}**")
                            st.table({"항목": [k for k, _ in step.details], "값": [str(v) for _, v in step.details]}, hide_index=True)

    if turns:
        st.markdown(_panels(rows[st.session_state.selected], turns[st.session_state.selected]), unsafe_allow_html=True)
