"""AI 게이트웨이 화면 (#76): 왼쪽 메뉴(열고 닫을 수 있음)로 기능을 고른다.

실행: uv run streamlit run admin/app.py   (게이트웨이와 Ollaya가 먼저 떠 있어야 한다)
프로그램 창으로 열기: scripts/open-app.ps1 (1200×800 안팎의 앱 창)

메뉴: 시연(챗봇 + 게이트웨이 처리 과정), 파일 검사. 요청 로그·통계·벤치마크·설정은 #78에서 더한다.
"""
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from admin import demo_page, files_page  # noqa: E402
from admin.ui import settings, sidebar_status  # noqa: E402

st.set_page_config(page_title="AI 게이트웨이", layout="wide", initial_sidebar_state="expanded")
st.logo(str(Path(__file__).resolve().parent / "logo.svg"), size="large")   # 메뉴 맨 위 프로그램 이름
nav = st.navigation([
    st.Page(demo_page.page, title="시연", icon=":material/terminal:", url_path="demo", default=True),
    st.Page(files_page.page, title="파일 검사", icon=":material/description:", url_path="files"),
], position="sidebar")
sidebar_status(settings())
nav.run()
