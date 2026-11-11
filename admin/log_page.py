"""요청 기록 화면 (#78): 게이트웨이를 거친 요청 목록과, 고른 요청의 처리 과정(시연 화면과 같은 다섯 단계).

요청 로그에는 가명화된 문장, 개인정보 종류·개수, 판단·경로·비용만 있다. 원문과 받은 답변은 남기지 않는다(설계 결정 2번).
화면 전체는 스크롤하지 않는다: 목록과 처리 과정은 각자 높이를 정하고 그 안에서만 스크롤한다 (11/09 의견).
"""
import html
from datetime import datetime

import streamlit as st

from admin import beta
from admin.flow import REASON_KO, ROUTE_KO
from admin.ui import LEVEL_KO, page_header, settings, steps_view
from gateway.storage.log import RequestLog

LIMIT = 200
FILTERS = {"전체": None, "템플릿": "template", "저렴한 모델": "cheap", "고성능 모델": "premium", "막음": "block"}


def _time(iso: str, fmt: str) -> str:
    return datetime.fromisoformat(iso).astimezone().strftime(fmt)


def _pii(counts: dict) -> str:
    return " · ".join(f"{k} {n}" for k, n in counts.items()) or "-"


def _detail(row: dict) -> str:
    reasons = " · ".join(REASON_KO.get(r, r) for r in row["reasons"]) or "-"
    model = row["model"] or "LLM 안 씀"
    tokens = f"{row['prompt_tokens']} → {row['completion_tokens']}" if row["model"] else "-"
    return ("<div class='gw-kv'>"
            f"<span>시각</span><b>{_time(row['created_at'], '%Y-%m-%d %H:%M:%S')}</b>"
            f"<span>보안 수준</span><b>{LEVEL_KO.get(row['security_level'], row['security_level'])}</b>"
            f"<span>가린 개인정보</span><b>{html.escape(_pii(row['pii_counts']))}</b>"
            f"<span>경로</span><b>{ROUTE_KO.get(row['route'], row['route'])} ({html.escape(reasons)})</b>"
            f"<span>모델 · 토큰</span><b>{html.escape(model)} · {tokens}</b>"
            f"<span>비용</span><b>${row['cost_usd']:.5f} (기존 방식 ${row['baseline_cost_usd']:.5f})</b>"
            f"<span>시간</span><b>판단 {row['decision_latency_ms']:.0f}ms · 전체 {row['total_latency_ms']:.0f}ms</b></div>")


def page() -> None:
    s = settings()
    log = RequestLog.from_config(s.config.storage)
    if beta.enabled(s):                                   # 체험판: 내가 보낸 요청만 (#90)
        page_header("요청 기록", "체험판: 내가 보낸 요청만 보입니다. 가명화된 문장과 판단·경로·비용만 남습니다.")
    else:
        page_header("요청 기록", f"최근 {LIMIT}건. 가명화된 문장과 판단·경로·비용만 남습니다 (원문과 답변은 남기지 않습니다).")
    choice = st.segmented_control("경로", list(FILTERS), default="전체", label_visibility="collapsed") or "전체"
    rows = beta.my_rows(log) if beta.enabled(s) else log.recent(LIMIT)
    if FILTERS[choice]:
        rows = [r for r in rows if r["route"] == FILTERS[choice]]
    if not rows:
        st.markdown("<div class='gw-hint'>해당하는 요청이 없습니다.</div>", unsafe_allow_html=True)
        return

    left, right = st.columns([1.35, 1], gap="medium")
    with left:
        event = st.dataframe({
            "시각": [_time(r["created_at"], "%m-%d %H:%M") for r in rows],
            "경로": [ROUTE_KO.get(r["route"], r["route"]) for r in rows],
            "요청 (가명화된 문장)": [r["text_masked"] or "(가리지 못한 값이 있어 남기지 않음)" for r in rows],
        }, hide_index=True, width="stretch", height=560, on_select="rerun", selection_mode="single-row",
            key=f"log_table_{choice}",
            column_config={"시각": st.column_config.TextColumn(width=88),
                           "경로": st.column_config.TextColumn(width=92),
                           "요청 (가명화된 문장)": st.column_config.TextColumn(width="large")})
    picked = event.selection.rows[0] if event.selection.rows else 0      # 고르지 않았으면 가장 최근 요청
    row = rows[picked]
    with right:
        st.markdown("<div class='gw-label'>고른 요청</div>" + _detail(row), unsafe_allow_html=True)
        st.markdown("<div class='gw-label'>처리 과정</div>", unsafe_allow_html=True)
        steps_view(row, s.config.decision.thresholds, height=300, key="log_steps")
