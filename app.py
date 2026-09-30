import streamlit as st

from src import appdata

st.set_page_config(page_title="반도체 산업 분석", page_icon="📊", layout="wide")

public = appdata.public_mode()
inds = appdata.industries()
with st.sidebar:
    st.selectbox("산업", list(inds), key="industry")
    cfg = appdata.current_industry()
    names = [c["name"] for c in cfg["companies"]]
    # 페이지 안에서 표의 행을 눌러 기업을 고른 경우 (위젯 생성 전에 반영해야 함)
    if st.session_state.get("pending_company") in names:
        st.session_state["company"] = st.session_state.pop("pending_company")
    if st.session_state.get("company") not in names:
        st.session_state["company"] = names[0]
    st.caption("기업은 각 페이지 제목 옆에서 고릅니다.")
    st.divider()
    if not public:
        # 개발·관리용 표시 (공개 모드에서는 숨김)
        st.caption("AI 요약: " + ("켜짐 ✅" if appdata.secret("ANTHROPIC_API_KEY") else "꺼짐 (API 키 없음)"))
        if appdata.secret("DART_API_KEY"):
            from src.dart import DAILY_LIMIT
            st.caption(f"오늘 DART 호출: {appdata.dart().usage_today():,} / {DAILY_LIMIT:,}건 (앱 자체 상한)")
        if appdata.secret("ECOS_API_KEY"):
            from src.ecos import DAILY_LIMIT as ECOS_LIMIT, EcosClient
            st.caption(f"오늘 ECOS 호출: {EcosClient(appdata.secret('ECOS_API_KEY')).usage_today():,} / {ECOS_LIMIT:,}건")
    st.caption("자료: DART 전자공시 · 한국은행 ECOS · 뉴스 링크. 주가는 직전 거래일 종가.")

pages = {
    "시작": [st.Page("views/home.py", title="홈", icon="🏠", default=True)],
    "산업": [
        st.Page("views/industry.py", title="산업 대시보드", icon="🏭"),
        st.Page("views/news.py", title="뉴스", icon="📰"),
    ],
    "기업": [
        st.Page("views/diagnosis.py", title="종합 진단", icon="📋"),
        st.Page("views/company.py", title="기업 분석", icon="🔎"),
        st.Page("views/quality.py", title="이익의 질", icon="🧪"),
        st.Page("views/anomaly.py", title="이상치 추적", icon="🔍"),
    ],
    "원문·자료": [
        st.Page("views/filings.py", title="사업·감사보고서", icon="📑"),
        st.Page("views/guide.py", title="지표 설명", icon="📘"),
    ],
}
if not public:
    # 증권사 리포트는 저작권 자료라 공개 모드에서는 메뉴에서 뺀다
    pages["원문·자료"].insert(1, st.Page("views/reports.py", title="증권사 리포트", icon="📈"))

pg = st.navigation(pages)
pg.run()
appdata.footer()
