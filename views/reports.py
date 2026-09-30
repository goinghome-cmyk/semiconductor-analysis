import pandas as pd
import streamlit as st

from src import appdata, reports
from src.llm import LLMError

cfg = appdata.current_industry()
names = [c["name"] for c in cfg["companies"]]

st.title("📈 증권사 리포트")
st.markdown(
    "리포트 PDF를 올리면 **추정 EPS·실적 전망·산업 전망**을 뽑아 저장합니다. "
    "저장한 추정 EPS는 '기업 분석'의 선행 PER 계산에, 산업 전망은 'AI 산업 브리핑'에 쓰입니다."
)
with st.expander("어떤 리포트를 얼마나 모아야 할까?"):
    st.markdown(
        "- **기간: 최근 3개월 이내**가 기본입니다. 추정치는 실적 발표 직후에 크게 수정되므로, "
        "가장 최근 분기 실적 발표 **이후**에 나온 리포트가 가장 유효합니다.\n"
        "- **기업 리포트**: 주요 기업별로 서로 다른 증권사 2~3개. 여러 곳의 평균을 써야 한 곳의 편향이 줄어듭니다.\n"
        "- **산업 리포트**: 2~3개. 업황 전망·가격 전망·수급 판단은 산업 리포트에 주로 있습니다.\n"
        "- 추정치 변화 추이(상향/하향)까지 보고 싶다면 같은 증권사의 리포트를 분기마다 1개씩, 1년 치 모으면 됩니다.\n"
        "- 리포트는 한경 컨센서스, 네이버페이 증권 리서치, 각 증권사 홈페이지에서 무료로 받을 수 있습니다."
    )

llm = appdata.llm()
if "report_results" not in st.session_state:
    st.session_state.report_results = {}

files = st.file_uploader("리포트 PDF 업로드 (여러 개 가능)", type="pdf", accept_multiple_files=True)
for f in files or []:
    with st.container(border=True):
        st.markdown(f"**{f.name}**")
        text = reports.pdf_text(f.getvalue())
        if len(text.strip()) < 200:
            st.warning("PDF에서 글자를 거의 읽지 못했습니다(스캔 이미지일 수 있음).")
        with st.expander("추출된 원문 보기"):
            st.text(text[:20_000])

        if llm is None:
            st.caption("💡 API 키가 없으면 자동 추출이 꺼집니다. 아래 '추정치 표'에 직접 입력할 수 있습니다.")
            continue
        if st.button("🤖 추정치·전망 추출", key=f"ext_{f.name}"):
            with st.spinner("리포트를 읽는 중…"):
                try:
                    data, _ = llm.extract(reports.EXTRACT_INSTRUCTION, text, reports.REPORT_SCHEMA)
                    st.session_state.report_results[f.name] = data
                except LLMError as e:
                    st.error(str(e))

        data = st.session_state.report_results.get(f.name)
        if not data:
            continue
        c1, c2, c3 = st.columns(3)
        c1.metric("증권사 / 발행일", f"{data.get('broker') or '-'}", data.get("report_date") or "", delta_color="off", delta_arrow="off")
        c2.metric("투자의견", data.get("rating") or "-")
        tp = data.get("target_price")
        c3.metric("목표주가", f"{tp:,.0f}원" if tp else "-")
        st.markdown("**핵심 논지**\n" + "\n".join(f"- {p}" for p in data.get("key_points", [])))
        if data.get("industry_outlook"):
            st.markdown(f"**산업 전망**: {data['industry_outlook']}")
        if data.get("risks"):
            st.markdown("**위험 요인**\n" + "\n".join(f"- {r}" for r in data["risks"]))

        new = reports.estimates_from_extraction(data)
        # 회사명을 설정 파일의 이름과 맞추기 (예: '삼성전자(005930)' -> '삼성전자')
        new["기업"] = new["기업"].map(lambda x: next((n for n in names if x and n in x), x))
        st.markdown("**추출된 추정치** (저장 전에 수정할 수 있습니다)")
        edited = st.data_editor(new, key=f"ed_{f.name}", hide_index=True, width="stretch")
        if st.button("💾 저장", key=f"save_{f.name}"):
            est = reports.load_estimates()
            reports.save_estimates(pd.concat([est, edited], ignore_index=True)
                                   .drop_duplicates(["기업", "증권사", "발행일", "연도"], keep="last"))
            reports.add_report_note(data)
            st.success("저장했습니다.")
        appdata.ai_disclaimer()

# ---------------------------------------------------------------- 추정치 표
st.subheader("저장된 추정치")
est = reports.load_estimates()
edited = st.data_editor(
    est, num_rows="dynamic", hide_index=True, width="stretch", key="est_table",
    column_config={"기업": st.column_config.SelectboxColumn(options=names)},
)
c1, c2, c3 = st.columns(3)
if c1.button("표 저장"):
    reports.save_estimates(edited)
    st.success("저장했습니다.")
c2.download_button("CSV 내려받기", edited.to_csv(index=False).encode("utf-8-sig"),
                   "estimates.csv", "text/csv")
up = c3.file_uploader("CSV 불러오기", type="csv", label_visibility="collapsed")
if up is not None and st.button("불러온 CSV로 교체"):
    reports.save_estimates(pd.read_csv(up))
    st.rerun()
st.caption("무료 클라우드 배포에서는 앱이 재시작되면 저장한 파일이 지워질 수 있습니다. 중요한 추정치는 CSV로 백업해 두세요.")

st.subheader("기업별 컨센서스 (저장된 리포트 평균)")
rows = []
for n in names:
    cons = reports.consensus(est, n)
    for y, r in cons.iterrows():
        rows.append({"기업": n, "연도": y, **r.to_dict()})
if rows:
    st.dataframe(pd.DataFrame(rows).style.format(
        {"매출액(억원)": "{:,.0f}", "영업이익(억원)": "{:,.0f}", "EPS(원)": "{:,.0f}",
         "목표주가(원)": "{:,.0f}", "리포트 수": "{:,.0f}"}, na_rep="-"),
        hide_index=True, width="stretch")
else:
    st.info("아직 저장된 추정치가 없습니다.")

notes = reports.load_report_notes()
if notes:
    st.subheader("저장된 리포트 전망")
    for n in sorted(notes, key=lambda x: x.get("report_date") or "", reverse=True):
        with st.expander(f"{n.get('report_date')} · {n.get('broker')} · {n.get('title')}"):
            st.markdown("\n".join(f"- {p}" for p in n.get("key_points", [])))
            if n.get("industry_outlook"):
                st.markdown(f"**산업 전망**: {n['industry_outlook']}")
