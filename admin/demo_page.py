"""시연 화면 (#76): 기존 챗봇(터미널 모양)의 API 주소만 게이트웨이로 바꾼 상태에서, 게이트웨이가 요청마다 한 일을 보여 준다.

- 챗봇은 OpenAI SDK로 게이트웨이를 부른다. 처리 과정은 응답 헤더의 요청 ID로 요청 로그를 읽어 만든다
- 로그에는 가명화된 문장만 있다. 원문(입력한 글)과 받은 답변은 이 화면의 세션에만 있다
- 메시지는 하나씩 보낸다 (앞 대화의 개인정보까지 세면 시연에서 헷갈린다)
- 화면 전체는 스크롤하지 않는다: 칸마다 높이를 정하고 안에서만 스크롤한다. "자세히"는 단계 옆의 작은 창으로 띄운다
  (11/09 사용자 의견: 자세히를 열거나 내용이 길어질 때 스크롤이 불편, 입력할 곳·커서가 잘 안 보임)
"""
import html
import re
from pathlib import Path

import streamlit as st
from openai import OpenAI, OpenAIError

from admin import beta
from admin.flow import answer_html, outbound_text, session_totals
from admin.money import krw, rate_line, saving_sentence
from admin.ui import NUMS, ROOT, gateway_url, page_header, settings, steps_view
from gateway.storage.log import RequestLog

TOKEN = re.compile(r"\[[^\[\]\s]{1,12}\d+\]")
FOCUS = """<script>
setTimeout(() => {
  const doc = (window.parent && window.parent.document) || document;
  const el = doc.querySelector('input[aria-label="메시지"]');
  if (el && doc.activeElement !== el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
}, 250);
</script>"""


def _client(s) -> OpenAI:
    return OpenAI(base_url=f"{gateway_url(s)}/v1", api_key="local-demo", max_retries=0, timeout=120)


def _system_prompt(s) -> str | None:
    """등록한 지시문(#73)이 있으면 함께 보낸다."""
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


def _pick(i: int, text: str) -> None:
    """예시 단추 (#131): 문장을 입력 칸에 넣기만 한다. 보내는 것은 사용자가 Enter로 (11/19 사용자 선택:
    어디에 쓰는지 익히고, 고쳐서 보낼 수도 있게). 단추 콜백은 입력 칸을 그리기 전에 돌아서 값을 바꿀 수 있다."""
    st.session_state.cli_text = text
    st.session_state.picked = i


def _submit() -> None:
    """보내기(Enter·단추). 보낼 문장을 꺼내 두고 입력 칸은 여기서 비운다. 폼의 clear_on_submit을 쓰면,
    한 번 보낸 뒤 예시 단추로 넣은 문장이 칸에는 보이는데 보낼 때는 빈 값으로 나갔다 (11/19 화면 확인)."""
    st.session_state.outgoing = st.session_state.cli_text.strip()
    st.session_state.cli_text = ""


def _examples(examples) -> None:
    """번호 붙은 예시 단추 (#131, 설문 D31: 10명 중 5명이 처음에 할 일을 몰랐다). 예시는 업종 프리셋의 display.examples.
    보낸 것은 초록 '완료', 다음에 할 것은 굵은 테두리. 상자 어디를 눌러도 단추가 눌린다(ui.CSS의 exbox)."""
    tried = st.session_state.tried
    nxt = next((i for i in range(len(examples)) if i not in tried), None)
    for i, (col, ex) in enumerate(zip(st.columns(len(examples), gap="small"), examples)):
        state = "done" if i in tried else "next" if i == nxt else "todo"
        with col.container(key=f"exbox_{state}_{i}"):
            st.button(f"{NUMS[i]} {ex.title}" + (" · 완료" if state == "done" else ""), key=f"exbtn_{i}",
                      on_click=_pick, args=(i, ex.text), help=ex.text)
            st.markdown(f"<div class='gw-ex-desc'>{html.escape(ex.desc)}</div>", unsafe_allow_html=True)


def _metrics(logged: list[dict], rate: float, note: str) -> str:
    """비용 카드 세 장 + 읽히는 한 문장 (#132: 설문에서 비용 표시가 3.0점으로 가장 낮았다). 금액은 원화."""
    cost, base = session_totals(logged)
    saved = f"{1 - cost / base:.0%}" if base else "-"
    return ("<div class='gw-metrics'>"
            f"<div class='gw-metric'><div class='k'>기존 방식 (전부 고성능 모델)</div><div class='v'>{krw(base, rate)}</div>"
            f"<div class='d'>이번 시연 요청 {len(logged)}건</div></div>"
            f"<div class='gw-metric'><div class='k'>가림게이트 사용</div><div class='v'>{krw(cost, rate)}</div>"
            "<div class='d'>정해 둔 답·저렴한 모델·차단 포함</div></div>"
            f"<div class='gw-metric save'><div class='k'>절감</div><div class='v'>{saved}</div>"
            f"<div class='d'>{krw(max(base - cost, 0), rate)} 아낌</div></div></div>"
            f"<div class='gw-saving'>{html.escape(saving_sentence(cost, base, len(logged), rate))}"
            f"<span>{html.escape(rate_line(rate, note))}</span></div>")


def _terminal(turns: list[dict], has_examples: bool) -> str:
    if not turns:
        first = ("위의 ① 단추를 누르면 문장이 아래 › 칸에 들어갑니다. Enter를 누르면 보냅니다." if has_examples
                 else "아래 › 칸에 메시지를 입력하고 Enter를 누르세요.")
        body = f"<div class='empty'>{first} 이 챗봇은 가림게이트를 거쳐 답합니다.</div>"
    else:
        body = "".join(
            f"<div><span class='meta'>[{i}]</span> <span class='u'>&gt; {html.escape(t['user'])}</span></div>"
            f"<div class='{'b blk' if t['route'] in ('block', 'error') else 'b'}'>{answer_html(t['answer'])}</div>"
            for i, t in enumerate(turns, 1))
    return f"<div class='gw-term'><div>{body}</div></div>"     # column-reverse 안쪽 한 덩어리 → 맨 아래부터 보인다


def _panels(row: dict | None, turn: dict) -> str:
    out = outbound_text(row) if row else None
    out_html = ("<span class='none'>외부로 나간 것 없음</span>" if out is None
                else TOKEN.sub(lambda m: f"<span class='gw-tok'>{m.group()}</span>", html.escape(out)))
    answer = answer_html(turn["answer"]) or "<span class='none'>-</span>"
    return ("<div style='display:flex;gap:10px'>"
            f"<div class='gw-panel out' style='flex:1'><h4>외부 AI 서버로 나간 텍스트</h4>{out_html}</div>"
            f"<div class='gw-panel in' style='flex:1'><h4>사용자가 받은 답변 (이 PC에서 복원)</h4>"
            f"<div style='white-space:pre-wrap'>{answer}</div></div></div>")


def _process(s, turns: list[dict], rows: list[dict | None]) -> None:
    is_beta = beta.enabled(s)
    if is_beta and turns:                     # 체험판 (#90): 보낸 뒤에는 안내를 작은 창으로 (화면을 밀지 않게)
        label, more = st.columns([3, 2], vertical_alignment="center")
        label.markdown("<div class='gw-label'>게이트웨이 처리 과정</div>", unsafe_allow_html=True)
        with more.popover(f"안내 · 남은 {beta.remaining(s)}회", width="stretch"):
            st.markdown(beta.GUIDE)
    else:
        st.markdown("<div class='gw-label'>게이트웨이 처리 과정</div>", unsafe_allow_html=True)
    if not turns:
        if is_beta:                           # 처음에는 비어 있는 이 칸에 안내를 둔다 (위에 두면 화면이 스크롤된다)
            with st.container(height=330, border=True, key="beta_guide"):
                st.markdown(beta.GUIDE)
        else:
            st.markdown("<div class='gw-hint'>요청을 보내면 단계별로 무엇을 했는지 여기에 나옵니다.</div>", unsafe_allow_html=True)
        return
    labels = [f"[{i}] {t['user'][:30]}" for i, t in enumerate(turns, 1)]
    sel = st.selectbox("요청", range(len(turns)), index=st.session_state.selected,
                       format_func=lambda i: labels[i], label_visibility="collapsed")
    st.session_state.selected = sel
    row = rows[sel]
    if row is None:
        st.warning("이 요청의 기록을 찾지 못했습니다 (게이트웨이 연결 실패).")
        return
    steps_view(row, s.config.decision.thresholds, height=300, key="steps", rate=s.config.display.krw_per_usd)   # 단계가 길어도 이 칸 안에서만 스크롤


def page() -> None:
    s = settings()
    examples = s.config.display.examples
    page_header("시연", f"기존 챗봇은 요청을 보내는 주소만 가림게이트(<code>{gateway_url(s)}/v1</code>)로 바꿨습니다."
                + (" 아래 예시를 번호 순서대로 보내 보세요." if examples else ""))
    st.session_state.setdefault("turns", [])
    st.session_state.setdefault("selected", None)
    st.session_state.setdefault("tried", set())          # 보낸 예시 번호 (#131)
    st.session_state.setdefault("picked", None)          # 입력 칸에 넣은 예시 번호. 보내면 tried로 옮긴다
    turns = st.session_state.turns
    log = RequestLog.from_config(s.config.storage)
    rows = [log.get(t["request_id"]) if t["request_id"] else None for t in turns]

    if (notice := st.session_state.pop("beta_notice", None)) is not None:   # 체험판 사용량 안내 (#90)
        st.warning(notice)
    st.markdown(_metrics([r for r in rows if r], s.config.display.krw_per_usd, s.config.display.rate_note),
                unsafe_allow_html=True)
    if examples:
        _examples(examples)
    left, right = st.columns([1.1, 1], gap="medium")
    with left:
        st.markdown("<div class='gw-label'>챗봇 (기존 서비스)</div>", unsafe_allow_html=True)
        st.markdown(_terminal(turns, bool(examples)), unsafe_allow_html=True)
        with st.container(key="cli_box"), st.form("cli", border=False):   # 터미널 모양은 이 칸에만 (ui.CSS)
            prompt, field, button = st.columns([0.3, 4, 1.5], vertical_alignment="center", gap="small")
            prompt.markdown("<div class='gw-prompt'>›</div>", unsafe_allow_html=True)
            field.text_input("메시지", placeholder="여기에 입력하고 Enter", label_visibility="collapsed", key="cli_text")
            button.form_submit_button("보내기", type="primary", width="stretch", on_click=_submit)
        if text := st.session_state.pop("outgoing", ""):
            if (refused := beta.allow_request(s)) is not None:      # 체험판 사용량 (#90)
                st.session_state.beta_notice = refused
            else:
                with st.spinner("게이트웨이가 처리하는 중"):
                    _send(s, text)
                beta.remember(st.session_state.turns[-1]["request_id"])
                if st.session_state.picked is not None:            # 예시를 고쳐서 보내도 그 예시는 해 본 것으로 (#131)
                    st.session_state.tried.add(st.session_state.picked)
                    st.session_state.picked = None
            st.rerun()
    with right:
        _process(s, turns, rows)
    if turns:
        st.markdown(_panels(rows[st.session_state.selected], turns[st.session_state.selected]), unsafe_allow_html=True)
    st.html(FOCUS, unsafe_allow_javascript=True)                     # 입력할 곳에 커서를 둔다 (열 때·보낸 뒤)
