"""파일 검사 화면 (#76, #50, 11/03 면담 의견): PDF·워드·엑셀·텍스트 파일 속 개인정보를 찾아 알려 준다.

게이트웨이의 `POST /v1/files/scan`을 부른다. 파일은 이 PC 밖으로 나가지 않고, 결과에는 값이 없다(종류·개수·위치·가명화된 텍스트).
"""
import base64
import html

import httpx
import streamlit as st

from admin import beta
from admin.ui import gateway_url, page_header, settings

TYPES = ["pdf", "docx", "xlsx", "txt", "csv"]


def page() -> None:
    s = settings()
    page_header("파일 검사", "PDF·워드·엑셀·텍스트 파일 속 개인정보를 찾습니다. 파일은 이 PC 밖으로 나가지 않습니다. "
                         "외부 AI에는 아래 가명화된 텍스트를 쓰세요.")
    if beta.enabled(s):                       # 웹 체험판 (#90)
        st.info("체험판: 가상의 정보가 든 파일만 올려 주세요. 파일은 운영자 PC에서 검사하고 저장하지 않습니다.")
    upload = st.file_uploader("검사할 파일 (10MB까지)", type=TYPES)
    if upload is None:
        return
    with st.spinner("검사하는 중"):
        try:
            r = httpx.post(f"{gateway_url(s)}/v1/files/scan", timeout=180,
                           json={"filename": upload.name, "content_base64": base64.b64encode(upload.getvalue()).decode()})
        except httpx.HTTPError as e:
            st.error(f"게이트웨이에 연결하지 못했습니다 ({type(e).__name__}). 왼쪽 상태를 확인하세요.")
            return
    if r.status_code != 200:
        msg = r.json().get("error", {}).get("message", r.text[:200]) if r.headers.get("content-type", "").startswith(
            "application/json") else r.text[:200]
        st.error(f"검사할 수 없습니다: {msg}")
        return
    rep = r.json()
    (st.warning if rep["has_pii"] else st.success)(rep["summary"])

    left, right = st.columns([1, 1.4], gap="medium")
    with left:
        st.markdown("<div class='gw-label'>찾은 개인정보 (종류별 서로 다른 값의 개수)</div>", unsafe_allow_html=True)
        if rep["counts"]:
            st.table({"종류": list(rep["counts"]), "개수": [str(v) for v in rep["counts"].values()]}, hide_index=True)
        else:
            st.caption("없음")
        if rep["findings"]:
            st.markdown("<div class='gw-label'>위치</div>", unsafe_allow_html=True)
            st.table({"위치": [f["where"] for f in rep["findings"]],
                      "종류": [", ".join(f"{k} {n}" for k, n in f["kinds"].items()) for f in rep["findings"]]},
                     hide_index=True)
    with right:
        st.markdown("<div class='gw-label'>가명화된 텍스트 (외부 AI에 붙여넣어도 되는 글)</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='gw-panel out' style='max-height:340px;overflow-y:auto;white-space:pre-wrap'>"
                    f"{html.escape(rep['masked_text'][:20000])}</div>", unsafe_allow_html=True)
        st.download_button("가명화된 텍스트 내려받기", rep["masked_text"].encode("utf-8"),
                           file_name=upload.name.rsplit(".", 1)[0] + "-가명화.txt", mime="text/plain")
