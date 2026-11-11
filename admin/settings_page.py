"""설정 화면 (#78, D26): 보안 수준·업종 프리셋·사용자 사전·등록 지시문을 화면에서 바꾸고 바로 적용한다.

11/09 사용자 의견: 설정 파일을 고친 뒤 다시 시작하는 것은 불편하다 → 저장하면 게이트웨이가 다음 요청부터 새 설정을 쓴다.
모델·주소·기준값은 측정으로 정한 값이라(D25) 화면에서는 보기만 한다.
사용자 사전에는 실제 고객 이름이 있어, 내용은 기본으로 숨기고 종류별 개수만 보인다 (시연 중 화면에 이름이 나오지 않게).
"""
from pathlib import Path

import streamlit as st
import yaml

from admin import beta
from admin import settings_store as store
from admin.ui import LEVEL_KO, PRESET_KO, gateway_url, health, page_header, settings
from gateway import config as gw_config

LEVEL_HELP = {
    "basic": "개인정보를 가명으로 바꿔 외부 AI에 보내고, 답변을 이 PC에서 원래 값으로 되돌립니다. "
             "개인정보의 실제 값이 있어야 하는 요청은 막습니다.",
    "strict": "기본에 더해, 기밀로 보이는 요청과 가린 뒤에도 주소가 남은 요청을 외부로 보내기 전에 막습니다.",
    "local": "민감한 요청을 이 PC의 AI가 처리하는 수준입니다. 로컬 AI 연결은 아직 준비 중이라 지금은 엄격과 같이 동작합니다.",
}


def _finish(result: store.Result) -> None:
    """저장 결과를 다음 화면에 보여 주고, 화면이 들고 있는 설정을 새로 읽게 한다."""
    st.session_state.settings_result = (result.ok, result.message)
    settings.clear()
    health.clear()
    st.rerun()


def _override_text(data: dict) -> str:
    return ("# 설정 화면에서 바꾼 값 (D26). config.yaml 위에 덮는다. 보안 수준·업종 프리셋·등록 지시문만 바꿀 수 있다\n"
            + yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


def _level_preset(s) -> None:
    left, right = st.columns(2, gap="large")
    with left:
        level = st.radio("보안 수준", store.LEVELS, index=store.LEVELS.index(s.config.security_level),
                         format_func=lambda v: LEVEL_KO[v])
        st.caption(LEVEL_HELP[level])
    with right:
        names = store.presets()
        current = s.config.preset if s.config.preset in names else names[0]
        preset = st.selectbox("업종 프리셋", names, index=names.index(current),
                              format_func=lambda v: f"{PRESET_KO.get(v, v)} ({v})")
        p = gw_config.load_preset(preset)
        faq = len(p.get("faq", {}).get("entries", []))
        m = p.get("masking", {})
        ids = ", ".join(c["kind"] for c in m.get("custom_patterns", [])) or "없음"
        st.caption(f"정해 둔 답(FAQ) {faq}개 · 회사별 식별번호: {ids} · 이름이 아닌 낱말 {len(m.get('not_names', []))}개. "
                   "업종을 바꾸면 이 값들이 함께 바뀝니다.")
    changed = level != s.config.security_level or preset != s.config.preset
    if st.button("저장하고 적용", type="primary", disabled=not changed, key="save_level"):
        data = store.override_with(store.read_override(), level=level, preset=preset)
        _finish(store.apply({gw_config.DEFAULT_OVERRIDE: _override_text(data)}, gateway_url(s)))


def _dictionary(s) -> None:
    path = store.resolve(s.config.masking.user_dictionary_path)
    if not path.exists():
        st.warning(f"사용자 사전 {s.config.masking.user_dictionary_path}이(가) 없어 게이트웨이가 시작하지 않습니다 (D21).")
        text = ""
    else:
        text = path.read_text(encoding="utf-8-sig")
    try:
        counts = store.check_dictionary(text)
        summary = " · ".join(f"{k} {v}개" for k, v in counts.items()) or "항목 없음"
    except ValueError as e:
        summary = f"형식 오류: {e}"
    source = {"example": "시연용 가상 사전", "custom": "회사 사전"}.get((health(gateway_url(s)) or {}).get("dictionary"), "-")
    st.markdown(f"<div class='gw-kv'><span>파일</span><code>{s.config.masking.user_dictionary_path}</code>"
                f"<span>지금 쓰는 사전</span><b>{source}</b><span>등록된 항목</span><b>{summary}</b></div>",
                unsafe_allow_html=True)
    st.caption("사전에 등록한 고객명·직원명·사내 고유명사는 [고객1]처럼 가려서 보냅니다. 사전에 없는 이름은 이름 인식 모델이 가립니다.")
    if not st.toggle("내용 보기·편집", value=False, help="실제 고객 이름이 들어 있어 기본으로 숨깁니다"):
        return
    new = st.text_area("사전 내용", value=text, height=200,
                       help="한 줄에 하나: 종류,값 (예: 고객,김민수 / 직원,이서연 / 사내,X-프로젝트). #으로 시작하는 줄은 설명")
    if st.button("저장하고 적용", type="primary", disabled=new == text, key="save_dict"):
        try:
            store.check_dictionary(new)
        except ValueError as e:
            st.error(f"저장하지 않았습니다: {e}")
            return
        _finish(store.apply({path: new}, gateway_url(s)))


def _prompt(s) -> None:
    registered = s.config.decision.trusted_system_prompts
    first = store.resolve(registered[0]) if registered else None
    text = first.read_text(encoding="utf-8-sig") if first and first.is_file() else ""
    status = f"등록됨 ({registered[0]}, {len(text)}자)" if registered else "등록 안 됨"
    st.markdown(f"<div class='gw-kv'><span>상태</span><b>{status}</b></div>", unsafe_allow_html=True)
    st.caption("운영하는 챗봇의 system 지시문을 등록합니다. 내용이 정확히 같은 system 메시지만 로컬 기밀 판단에서 뺍니다 "
               "(배송비·할인율 같은 정책 문장을 기밀로 보는 것을 막는다, #73). 위험 판단은 그대로 거칩니다. 시연 챗봇도 이 지시문을 씁니다.")
    if len(registered) > 1:
        st.caption(f"config.yaml에 등록한 지시문 {len(registered)}개 중 첫 번째를 보여 줍니다. 저장하면 이 지시문 하나로 바뀝니다.")
    new = st.text_area("등록 지시문", value=text, height=200, placeholder="예: 너는 온라인 쇼핑몰의 고객 상담원이다. ...")
    save, clear = st.columns([1, 1])
    prompt_path = store.resolve(store.PROMPT_FILE)
    if save.button("저장하고 적용", type="primary", disabled=new.strip() == text.strip() or not new.strip(), key="save_prompt"):
        data = store.override_with(store.read_override(), prompts=[store.PROMPT_FILE])
        _finish(store.apply({prompt_path: new, gw_config.DEFAULT_OVERRIDE: _override_text(data)}, gateway_url(s)))
    if clear.button("등록 해제", disabled=not registered, key="clear_prompt"):
        data = store.override_with(store.read_override(), prompts=[])
        _finish(store.apply({prompt_path: None, gw_config.DEFAULT_OVERRIDE: _override_text(data)}, gateway_url(s)))


def _models(s) -> None:
    c = s.config
    st.table({"역할": ["로컬 판단 (기밀)", "이름 인식", "클라우드 판단", "저렴한 모델", "고성능 모델"],
              "모델": [c.decision.model, Path(c.masking.ner.model_dir).name, c.decision.cloud.model,
                       c.llm.cheap.model, c.llm.premium.model],
              "어디서": ["이 PC (Ollaya, CPU)", "이 PC (CPU)", "OpenAI (가명화된 문장만)", "OpenAI", "OpenAI"]},
             hide_index=True)
    t = c.decision.thresholds
    st.table({"판단": ["기밀", "원본 값 필요", "위험", "복잡 (고성능으로)"],
              "기준값": [t.get("is_confidential"), t.get("needs_raw_value"), t.get("is_risky"), t.get("difficulty_complex")]},
             hide_index=True)
    st.caption("모델·주소·기준값은 측정으로 정한 값이라(D25) 화면에서 바꾸지 않습니다. config.yaml에서 바꾸고 게이트웨이를 다시 시작합니다.")


def _level_preset_view(s) -> None:
    """체험판 (#90): 보기만. 테스터가 게이트웨이 하나를 같이 쓰므로 바꾸면 모두에게 적용된다."""
    level, preset = s.config.security_level, s.config.preset
    st.markdown(f"<div class='gw-kv'><span>보안 수준</span><b>{LEVEL_KO[level]}</b>"
                f"<span>업종 프리셋</span><b>{PRESET_KO.get(preset, preset)}</b></div>", unsafe_allow_html=True)
    st.table({"보안 수준": [LEVEL_KO[v] for v in store.LEVELS], "동작": [LEVEL_HELP[v] for v in store.LEVELS]},
             hide_index=True)
    st.caption("설치형에서는 이 화면에서 보안 수준·업종·사용자 사전·등록 지시문을 바꾸면 다시 시작하지 않고 바로 적용됩니다.")


def page() -> None:
    s = settings()
    if beta.enabled(s):                       # 웹 체험판 (#90): 보기만. 사용자 사전·등록 지시문은 보이지 않는다
        page_header("설정", "체험판에서는 설정을 볼 수만 있습니다. 모든 테스터가 운영자가 정한 같은 설정을 씁니다.")
        tabs = st.tabs(["보안 수준·업종", "모델"], key="settings_tab_beta", on_change="rerun")
        with tabs[0]:
            _level_preset_view(s)
        with tabs[1]:
            _models(s)
        return
    page_header("설정", "저장하면 게이트웨이에 바로 적용됩니다. 다시 시작하지 않아도 다음 요청부터 새 설정을 씁니다.")
    if (res := st.session_state.pop("settings_result", None)) is not None:
        (st.success if res[0] else st.error)(res[1])
    # 저장·입력으로 화면을 다시 그려도 보던 탭에 머문다 (on_change="ignore"면 서버가 고른 탭을 모른다)
    tabs = st.tabs(["보안 수준·업종", "사용자 사전", "등록 지시문", "모델 (보기)"], key="settings_tab", on_change="rerun")
    with tabs[0]:
        _level_preset(s)
    with tabs[1]:
        _dictionary(s)
    with tabs[2]:
        _prompt(s)
    with tabs[3]:
        _models(s)
