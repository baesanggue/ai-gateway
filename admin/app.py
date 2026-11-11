"""AI 게이트웨이 화면 (#76, #78): 왼쪽 메뉴(열고 닫을 수 있음)로 기능을 고른다.

실행: uv run streamlit run admin/app.py   (게이트웨이와 Ollaya가 먼저 떠 있어야 한다)
프로그램 창으로 열기: scripts/open-app.ps1 (1200×800 안팎의 앱 창)

메뉴 (11/09 사용자 선택): 현황(처음 화면) / 시연 / 파일 검사 / 요청 기록 / 설정 — admin/nav.py
"""
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from admin import beta  # noqa: E402
from admin.nav import pages  # noqa: E402
from admin.ui import settings, sidebar_status  # noqa: E402

st.set_page_config(page_title="가림게이트", layout="wide", initial_sidebar_state="expanded")
st.logo(str(Path(__file__).resolve().parent / "logo.svg"), size="large")   # 메뉴 맨 위 프로그램 이름
beta.gate(settings())                     # 웹 체험판이면 비밀번호부터 (#90). 통과 전에는 메뉴도 그리지 않는다
nav = st.navigation(list(pages().values()), position="sidebar")
sidebar_status(settings())
nav.run()
