"""왼쪽 메뉴 (#78, 11/09 사용자 선택): 현황(처음 화면) / 시연 / 파일 검사 / 요청 기록 / 설정.

다른 화면에서 메뉴로 보내는 링크(st.page_link)도 여기서 같은 페이지를 가져다 쓴다. 페이지는 주소(url_path)로 구별된다.
"""
import streamlit as st


def pages() -> dict[str, st.Page]:
    from admin import demo_page, files_page, log_page, overview_page, settings_page
    return {
        "overview": st.Page(overview_page.page, title="현황", icon=":material/dashboard:", url_path="overview", default=True),
        "demo": st.Page(demo_page.page, title="시연", icon=":material/terminal:", url_path="demo"),
        "files": st.Page(files_page.page, title="파일 검사", icon=":material/description:", url_path="files"),
        "log": st.Page(log_page.page, title="요청 기록", icon=":material/list_alt:", url_path="log"),
        "settings": st.Page(settings_page.page, title="설정", icon=":material/settings:", url_path="settings"),
    }
