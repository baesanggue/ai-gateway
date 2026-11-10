"""현황 화면 (#78): 프로그램을 열면 처음 보이는 화면. 게이트웨이를 거친 모든 요청의 누적을 한눈에 본다.

11/09 사용자 선택: 메인은 현황, 시연은 메뉴 하나로. 요청 로그에는 가명화된 문장과 종류·개수만 있다(값 없음).
- 숫자 카드 넷: 처리한 요청, 비용(기존 방식 vs 게이트웨이), LLM을 부르지 않고 끝난 요청, 가린 개인정보
- 경로별 요청과 막은 이유: 가로 막대 (한 계열이라 한 색, 값은 옆에 글자로)
- 최근 요청 다섯 건 → 요청 기록 메뉴에서 전부 본다
비용은 LLM 비용 기준이다. 판단 API 비용은 190행에 $0.01로 작아(#59) 넣지 않았다.
"""
import html
from datetime import datetime

import streamlit as st

from admin.flow import REASON_KO, ROUTE_KO
from admin.ui import bars, page_header, settings
from gateway.storage.log import RequestLog

ROUTES = ("template", "cheap", "premium", "block")


def _tiles(t: dict, b: dict) -> str:
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
            f"<div class='d'>기존 방식 ${base:.4f} → 게이트웨이 ${cost:.4f}</div></div>"
            f"<div class='gw-metric'><div class='k'>LLM 없이 끝남</div><div class='v'>{no_llm}건</div>"
            f"<div class='d'>템플릿 {by_route.get('template', 0)} · 막음 {by_route.get('block', 0)}</div></div>"
            f"<div class='gw-metric'><div class='k'>가린 개인정보</div><div class='v'>{sum(b['pii'].values())}개</div>"
            f"<div class='d'>{html.escape(pii_top)}</div></div></div>")


def _box(title: str, body: str) -> str:
    return f"<div class='gw-box'><h4>{title}</h4>{body}</div>"


def _time(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone().strftime("%m-%d %H:%M")


def page() -> None:
    s = settings()
    page_header("현황", "게이트웨이를 거친 모든 요청의 누적입니다. 요청 기록에는 가명화된 문장만 남습니다.")
    log = RequestLog.from_config(s.config.storage)
    t, b = log.totals(), log.breakdown()
    st.markdown(_tiles(t, b), unsafe_allow_html=True)

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
    recent = log.recent(5)
    if not recent:
        st.markdown("<div class='gw-hint'>요청이 들어오면 여기에 나옵니다.</div>", unsafe_allow_html=True)
        return
    st.dataframe({
        "시각": [_time(r["created_at"]) for r in recent],
        "경로": [ROUTE_KO.get(r["route"], r["route"]) for r in recent],
        "요청 (가명화된 문장)": [r["text_masked"] or "(가리지 못한 값이 있어 남기지 않음)" for r in recent],
        "비용": [f"${r['cost_usd']:.5f}" for r in recent],
    }, hide_index=True, width="stretch", height=213)
