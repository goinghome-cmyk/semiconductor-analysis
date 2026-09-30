from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src import anomaly, appdata, industry
from src.dart import DartError

cfg = appdata.current_industry()
comp = appdata.company_header("이상치 추적", "🔍")
name = comp["name"]
appdata.terms("anomaly")

st.markdown(
    "숫자가 **평소와 다르게 움직인 분기**를 찾고, 그 이유를 다른 자료에서 찾습니다: "
    "그 분기 **보고서 원문** · 같은 시기 **공시** · 같은 시기 **뉴스** · 같은 시기 **업계 흐름**."
)
with st.expander("감지 기준"):
    st.markdown(
        f"- 매출 급변: 전분기 대비 ±{anomaly.TH_REV*100:.0f}% 이상\n"
        f"- 이익률 급변: 영업이익률이 전분기 대비 ±{anomaly.TH_OPM*100:.0f}%p 이상\n"
        f"- 재고 급증: 재고자산이 전분기 대비 +{anomaly.TH_INV*100:.0f}% 이상\n"
        "- 과거 실적 재작성: 공시된 3개월 매출과 누적 매출의 차이가 3% 넘게 불일치\n"
        "- 흑자인데 현금 유출: 분기 영업이익은 흑자인데 영업현금흐름은 적자\n\n"
        "최근 8개 분기를 봅니다. 감지는 '확인해 볼 곳'을 알려줄 뿐 문제가 있다는 뜻은 아닙니다."
    )

appdata.require_dart()
data = appdata.load_all(cfg)
gts = industry.group_timeseries(cfg, data)

# ================================================================ 업계 전체
st.subheader("업계 전체 최근 이상치")
rows = []
for c in cfg["companies"]:
    d = data.get(c["name"])
    if not d or d["quarterly"].empty:
        continue
    for a in anomaly.detect(d["quarterly"], last_n=4):
        rows.append({"기업": c["name"], "분기": a.period, "종류": anomaly.KINDS[a.kind], "내용": a.value})
if rows:
    appdata.pick_company_table(pd.DataFrame(rows).sort_values(["분기", "기업"], ascending=[False, True])
                               .reset_index(drop=True), key="anomaly_table")
else:
    st.info("최근 4개 분기에 감지된 이상치가 없습니다.")

# ================================================================ 선택 기업
st.divider()
st.header(name)
d = data.get(name)
if not d or d["quarterly"].empty:
    st.warning("데이터가 없습니다.")
    st.stop()
q = d["quarterly"]
found = anomaly.detect(q)

fig = go.Figure()
fig.add_bar(x=q.index, y=q["revenue"] / 1e8, name="매출(억)", marker_color="#9db4d6")
fig.add_scatter(x=q.index, y=q["opm"] * 100, name="영업이익률(%)", yaxis="y2", mode="lines+markers",
                line=dict(color="#d9822b", width=3))
for a in found:
    fig.add_vline(x=a.period, line=dict(color="#c0392b", dash="dot", width=1))
fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=-0.25),
                  yaxis2=dict(overlaying="y", side="right", showgrid=False))
st.plotly_chart(fig, width="stretch")
st.caption("빨간 점선 = 감지된 이상치 분기")

if not found:
    st.success("최근 8개 분기에 감지된 이상치가 없습니다.")
    st.stop()

labels = [a.label for a in found]
pick = st.radio("추적할 이상치", labels, index=0)
a = found[labels.index(pick)]
start, end, report_period = anomaly.period_dates(a.period)

# ---------------------------------------------------------------- 업계와 비교 (DART 호출 없음)
with st.container(border=True):
    st.markdown("**🏭 같은 분기 업계는?**")
    grp = comp["group"]
    gq = gts[(gts["그룹"] == grp) & (gts["분기"] == a.period)]
    row = q.loc[a.period]
    if not gq.empty and a.kind in ("rev", "margin"):
        g = gq.iloc[0]
        if a.kind == "rev":
            mine, theirs = row.get("rev_yoy"), g.get("revenue_yoy")
            txt = f"{name} 매출 YoY {mine*100:+.0f}% vs {grp} 그룹 {theirs*100:+.0f}%" \
                if pd.notna(mine) and pd.notna(theirs) else None
        else:
            prev = gts[(gts["그룹"] == grp)].set_index("분기")["opm"].shift(1).get(a.period)
            mine, theirs = row["opm"] - q["opm"].shift(1).loc[a.period], g["opm"] - prev if pd.notna(prev) else None
            txt = f"{name} 영업이익률 변화 {mine*100:+.1f}%p vs {grp} 그룹 {theirs*100:+.1f}%p" \
                if theirs is not None and pd.notna(theirs) else None
        if txt:
            same = (mine > 0) == (theirs > 0) and abs(theirs) >= abs(mine) * 0.5
            st.markdown(f"{txt} → **{'업계 공통 흐름에 가까움' if same else '이 기업만의 요인일 가능성'}**")
            st.caption("같은 방향이고 업계 변화 폭이 기업 변화의 절반 이상이면 '업계 공통'으로 봅니다.")
        else:
            st.caption("비교할 업계 데이터가 부족합니다.")
    else:
        st.caption("이 종류의 이상치는 업계 비교 대신 원문·공시를 확인하세요.")

# ---------------------------------------------------------------- 원인 추적 (DART·뉴스)
# 이상치를 고르면 바로 불러온다. 한 번 받은 보고서·공시는 디스크 캐시에 남고,
# 최근 이상치 분기는 scripts/refresh_data.py 가 미리 받아 두어 대부분 DART 호출 없이 열린다.
with st.spinner("보고서 원문·공시·뉴스를 모으는 중… (처음 여는 분기는 10초 안팎)"):
    try:
        rep = appdata.period_report_blocks(comp["stock_code"], report_period)
        disc = appdata.disclosures_between(comp["stock_code"], (start - timedelta(days=30)).strftime("%Y%m%d"),
                                           (end + timedelta(days=75)).strftime("%Y%m%d"))
    except DartError as e:
        st.error(str(e))
        st.stop()
    try:
        nws = appdata.news_between(name, start.isoformat(), (end + timedelta(days=60)).isoformat())
    except Exception:
        nws = pd.DataFrame()

paras = anomaly.rank_paragraphs(rep["blocks"], a.kind) if rep else []
ev = anomaly.events(disc)
heads = nws["제목"].tolist() if not nws.empty else []

with st.container(border=True):
    st.markdown("**🔗 모은 자료에서 보이는 단서**")
    for c_ in anomaly.clues(a.kind, paras, ev, heads):
        st.markdown(f"- {c_}")
    st.caption("단어 규칙으로 뽑은 단서입니다. 결론이 아니라 확인할 방향입니다.")

st.subheader(f"📄 {rep['report_nm'] if rep else report_period + ' 보고서'} — 관련 문단")
if not rep:
    st.caption("해당 기간 보고서를 찾지 못했습니다.")
elif not paras:
    st.caption("관련 키워드가 많은 문단을 찾지 못했습니다. (분기보고서는 경영진단을 생략하는 경우가 많습니다)")
else:
    st.caption(f"[DART 원문 보기](https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rep['rcept_no']}) · 키워드가 많이 나온 순")
    for p, score in paras:
        with st.container(border=True):
            st.markdown(anomaly.highlight(p[:900] + ("…" if len(p) > 900 else ""), a.kind))

st.subheader("📋 같은 시기 공시")
st.caption(f"{(start - timedelta(days=30)):%Y-%m-%d} ~ {(end + timedelta(days=75)):%Y-%m-%d} · ⭐ = 분할·합병·계약 등 눈여겨볼 제목")
if ev.empty:
    st.caption("정기보고서·지분공시를 제외한 공시가 없습니다.")
else:
    st.dataframe(ev, hide_index=True, width="stretch",
                 column_config={"링크": st.column_config.LinkColumn("링크", display_text="열기")})

st.subheader("📰 같은 시기 뉴스")
if nws.empty:
    st.caption("검색된 기사가 없습니다.")
else:
    for r in nws.head(15).itertuples():
        st.markdown(f"- [{r.제목}]({r.링크}) · {r.언론사} · {r.날짜:%Y-%m-%d}")
