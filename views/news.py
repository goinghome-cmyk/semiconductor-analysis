import streamlit as st

from src import appdata
from src.llm import LLMError

cfg = appdata.current_industry()
comp = appdata.company_header("뉴스", "📰")
name = comp["name"]

c1, c2 = st.columns([3, 1])
options = [f"{name} (선택 기업)"] + cfg["news_keywords"]
choice = c1.selectbox("검색어", options)
days = c2.selectbox("기간", [7, 14, 30], index=1, format_func=lambda d: f"최근 {d}일")
query = name if choice.endswith("(선택 기업)") else choice
custom = st.text_input("직접 검색 (선택)", placeholder="예: 삼성전자 HBM4")
if custom:
    query = custom

try:
    df = appdata.news_search(query, days)
except Exception as e:
    st.error(f"뉴스를 불러오지 못했습니다: {e}")
    st.stop()

if df.empty:
    st.info("검색 결과가 없습니다.")
    st.stop()

llm = appdata.llm()
if llm is not None and st.button("🤖 주요 이슈 정리", type="primary"):
    doc = "\n".join(f"- [{r.날짜:%m/%d}] {r.제목} ({r.언론사})" for r in df.itertuples())
    with st.spinner("뉴스 제목을 분석하는 중…"):
        try:
            text, _ = llm.ask(
                f"'{query}' 관련 최근 뉴스 제목 목록입니다. 제목만으로 판단할 수 있는 범위에서 "
                "1) 반복해서 나오는 주요 이슈 3~5개(각각 관련 기사 수와 긍정/부정/중립 톤) "
                f"2) {cfg['industry']} 산업 또는 기업 실적에 미칠 수 있는 영향 3) 기사 본문으로 확인이 필요한 점 을 정리하세요.",
                doc,
            )
            st.markdown(text)
            appdata.ai_disclaimer()
        except LLMError as e:
            st.error(str(e))

st.caption(f"구글 뉴스 검색 결과 {len(df)}건")
for r in df.itertuples():
    st.markdown(f"**[{r.제목}]({r.링크})**  \n{r.언론사} · {r.날짜:%Y-%m-%d %H:%M}")
