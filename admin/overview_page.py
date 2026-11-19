"""현황 화면 (#78): 프로그램을 열면 처음 보이는 화면. 게이트웨이를 거친 모든 요청의 누적을 한눈에 본다.

11/09 사용자 선택: 메인은 현황, 시연은 메뉴 하나로. 요청 로그에는 가명화된 문장과 종류·개수만 있다(값 없음).
- 숫자 카드 넷: 처리한 요청, 비용(기존 방식 vs 게이트웨이), LLM을 부르지 않고 끝난 요청, 가린 개인정보
- 경로별 요청과 막은 이유: 가로 막대 (한 계열이라 한 색, 값은 옆에 글자로)
- 최근 요청 다섯 건 → 요청 기록 메뉴에서 전부 본다
- 맨 위 첫 화면 안내 (#131): 요청이 없으면 세 단계를 펼쳐 두고, 있으면 "처음 쓰는 방법 보기" 한 줄로 접는다
비용은 LLM 비용 기준이다. 판단 API 비용은 190행에 $0.01로 작아(#59) 넣지 않았다.
"""
import html
from datetime import datetime

import streamlit as st

from admin import beta
from admin.flow import REASON_KO, ROUTE_KO
from admin.money import krw, rate_line, saving_sentence
from admin.stats import summarize
from admin.ui import NUMS, bars, page_header, settings
from gateway.storage.log import RequestLog

ROUTES = ("template", "cheap", "premium", "block")


def _tiles(t: dict, b: dict, rate: float, note: str) -> str:
    base, cost = t["baseline_cost_usd"], t["cost_usd"]
    saved = f"{1 - cost / base:.0%}" if base else "-"
    by_route = t["by_route"]
    no_llm = by_route.get("template", 0) + by_route.get("block", 0)
    pii = sorted(b["pii"].items(), key=lambda kv: -kv[1])
    pii_top = " · ".join(f"{k} {n}" for k, n in pii[:3]) or "없음"
    return ("<div class='gw-metrics'>"
            f"<div class='gw-metric'><div class='k'>처리한 요청</div><div class='v'>{t['requests']}건</div>"
            f"<div class='d'>오늘 {b['today']}건</div></div>"
            f"<div class='gw-metric save'><div class='k'>비용 절감</div><div class='v'>{saved}</div>"
            f"<div class='d'>기존 방식 {krw(base, rate)} → 가림게이트 {krw(cost, rate)}</div></div>"
            f"<div class='gw-metric'><div class='k'>LLM 없이 끝남</div><div class='v'>{no_llm}건</div>"
            f"<div class='d'>템플릿 {by_route.get('template', 0)} · 막음 {by_route.get('block', 0)}</div></div>"
            f"<div class='gw-metric'><div class='k'>가린 개인정보</div><div class='v'>{sum(b['pii'].values())}개</div>"
            f"<div class='d'>{html.escape(pii_top)}</div></div></div>"
            f"<div class='gw-saving'>{html.escape(saving_sentence(cost, base, t['requests'], rate))}"
            f"<span>{html.escape(rate_line(rate, note))}</span></div>")


def _steps_html(examples) -> str:
    """처음 쓰는 사람을 위한 세 단계 (#131). 2단계는 업종 프리셋의 시연 예시(display.examples)로 채운다."""
    flow = " → ".join(f"{NUMS[i]} {html.escape(e.title)}" for i, e in enumerate(examples))
    second = (("예시를 차례로 보냅니다", f"{flow}. 누르면 문장이 입력 칸에 들어가고, Enter로 보냅니다.")
              if examples else ("요청을 보내 봅니다", "입력 칸에 요청을 쓰고 Enter를 누릅니다."))
    steps = [("시연 메뉴를 엽니다", "왼쪽 메뉴의 <b>시연</b>입니다. 기존 챗봇이 가림게이트를 거쳐 답하는 화면입니다."),
             second,
             ("무엇이 가려졌는지 봅니다", "외부로 나간 글과 받은 답변을 비교하고, 이 화면에서 아낀 비용을 확인합니다.")]
    return "<div class='gw-gsteps'>" + "".join(
        f"<div class='gw-gstep'><div class='t'><span class='n'>{n}</span>{title}</div><div class='s'>{body}</div></div>"
        for n, (title, body) in enumerate(steps, 1)) + "</div>"


def _close_guide() -> None:
    st.session_state.guide_closed = True


def _guide(requests: int, examples) -> None:
    """첫 화면 안내 (#131, 11/19 사용자 선택 A). 아직 요청이 없으면 맨 위에 펼쳐 두고,
    요청이 생기거나 닫으면 접힌 한 줄로 줄인다. 닫은 것은 이 브라우저 세션에서만 기억한다."""
    from admin.nav import pages
    if requests == 0 and not st.session_state.get("guide_closed"):
        with st.container(key="guide"):
            st.markdown("<div class='gw-guide-hd'>처음 쓰시나요? 세 단계면 가림게이트가 하는 일을 볼 수 있습니다</div>"
                        + _steps_html(examples), unsafe_allow_html=True)
            go, close, _ = st.columns([1.4, 0.6, 4], vertical_alignment="center", gap="small")
            go.page_link(pages()["demo"], label="시연 시작하기", icon=":material/arrow_forward:")
            close.button("닫기", key="guide_close", on_click=_close_guide)
    else:
        with st.expander("처음 쓰는 방법 보기", icon=":material/help:"):
            st.markdown(_steps_html(examples), unsafe_allow_html=True)
            st.page_link(pages()["demo"], label="시연 시작하기", icon=":material/arrow_forward:")


def _box(title: str, body: str) -> str:
    return f"<div class='gw-box'><h4>{title}</h4>{body}</div>"


def _time(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone().strftime("%m-%d %H:%M")


def page() -> None:
    s = settings()
    log = RequestLog.from_config(s.config.storage)
    if beta.enabled(s):                                   # 체험판: 내가 보낸 요청만 (#90)
        page_header("현황", "체험판: 내가 보낸 요청만 보입니다. 요청 기록에는 가명화된 문장만 남습니다.")
        mine = beta.my_rows(log)
        (t, b), recent = summarize(mine), mine[:5]
    else:
        page_header("현황", "게이트웨이를 거친 모든 요청의 누적입니다. 요청 기록에는 가명화된 문장만 남습니다.")
        t, b, recent = log.totals(), log.breakdown(), log.recent(5)
    _guide(t["requests"], s.config.display.examples)
    rate = s.config.display.krw_per_usd
    st.markdown(_tiles(t, b, rate, s.config.display.rate_note), unsafe_allow_html=True)

    left, right = st.columns(2, gap="medium")
    route_items = [(ROUTE_KO[r], t["by_route"].get(r, 0)) for r in ROUTES]
    left.markdown(_box("경로별 요청", bars(route_items) if t["requests"] else
                       "<div class='empty'>아직 요청이 없습니다. 시연 메뉴에서 보내 보세요.</div>"), unsafe_allow_html=True)
    reasons = sorted(b["block_reasons"].items(), key=lambda kv: -kv[1])[:5]
    right.markdown(_box("막은 이유 (많은 순)", bars([(REASON_KO.get(k, k), n) for k, n in reasons]) if reasons else
                        "<div class='empty'>막은 요청이 없습니다.</div>"), unsafe_allow_html=True)
    st.caption("비용은 LLM 비용 기준입니다. 기존 방식은 모든 요청을 고성능 모델로 보냈을 때의 추정입니다.")

    head, link = st.columns([3, 1], vertical_alignment="bottom")
    head.markdown("<div class='gw-label'>최근 요청</div>", unsafe_allow_html=True)
    from admin.nav import pages
    link.page_link(pages()["log"], label="요청 기록에서 모두 보기", icon=":material/arrow_forward:")
    if not recent:
        st.markdown("<div class='gw-hint'>요청이 들어오면 여기에 나옵니다.</div>", unsafe_allow_html=True)
        return
    st.dataframe({
        "시각": [_time(r["created_at"]) for r in recent],
        "경로": [ROUTE_KO.get(r["route"], r["route"]) for r in recent],
        "요청 (가명화된 문장)": [r["text_masked"] or "(가리지 못한 값이 있어 남기지 않음)" for r in recent],
        "비용": [krw(r['cost_usd'], rate) for r in recent],
    }, hide_index=True, width="stretch", height=213)
