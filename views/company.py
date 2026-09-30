from datetime import date

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src import appdata, industry, metrics, reports, sensitivity
from src.dart import DartError
from src.appdata import fmt_pct, fmt_won, fmt_x
from src.cashflow import classify

cfg = appdata.current_industry()
comp = appdata.company_header("기업 분석", "🔎")
name = comp["name"]
appdata.terms("company")

appdata.require_dart()
data = appdata.load_all(cfg)
d = data.get(name)
if not d or d["quarterly"].empty:
    st.error(f"{name}의 재무 데이터를 불러오지 못했습니다.")
    st.stop()

q, a = d["quarterly"], d["annual"]
last = q.iloc[-1]
snap = appdata.snapshot(cfg, data)
group = comp["group"]
# 비교 기준: 같은 밸류체인 그룹 (칩메이커끼리, 장비끼리…)
gts = industry.group_timeseries(cfg, data)

st.caption(f"{group} · {comp['segment']} · 종목코드 {comp['stock_code']} · 최근 분기 {q.index[-1]} · "
           f"{'연결' if d['fs_div'] == 'CFS' else '별도'}재무제표 기준 · 비교 대상: {group} 경쟁사 평균(자기 제외)")

# ---------------------------------------------------------------- 주가·밸류에이션
price, price_date = appdata.latest_price(comp["stock_code"])
est = reports.load_estimates()
cons = reports.consensus(est, name)
this_year = date.today().year
fwd_eps = None
if not cons.empty:
    for y in (this_year, this_year + 1):
        if y in cons.index and pd.notna(cons.loc[y, "EPS(원)"]):
            fwd_eps, fwd_year = cons.loc[y, "EPS(원)"], y
            break
val = metrics.valuation(last, d["shares"], price, fwd_eps)

st.subheader("밸류에이션")
st.caption(f"주가: {price_date} 종가 {price:,.0f}원" if price else "주가를 불러오지 못했습니다.")


peers = snap[(snap["그룹"] == group) & (snap["기업"] != name)]


def vs(v, col, fmt):
    """자기 자신을 뺀 같은 그룹 경쟁사 평균과 범위. 그룹이 3곳 안팎이라 중앙값 대신 평균,
    한 곳이 튀면 평균이 끌려가므로 범위를 함께 표시."""
    s = pd.to_numeric(peers[col], errors="coerce").dropna()
    if v is None or pd.isna(v) or s.empty:
        return None
    rng = f" (범위 {fmt(s.min())}~{fmt(s.max())})" if len(s) >= 2 else ""
    return f"경쟁사 {len(s)}곳 평균 {fmt(s.mean())}{rng}"


c1, c2, c3 = st.columns(3)
c1.metric("PER (TTM)", fmt_x(val["per"]), vs(val["per"], "PER", fmt_x), delta_color="off", delta_arrow="off", help=appdata.term_help("PER"))
c2.metric("PBR", fmt_x(val["pbr"]), vs(val["pbr"], "PBR", fmt_x), delta_color="off", delta_arrow="off", help=appdata.term_help("PBR"))
c3.metric("ROE (TTM)", fmt_pct(last.get("roe")), vs(last.get("roe"), "ROE", fmt_pct), delta_color="off", delta_arrow="off", help=appdata.term_help("ROE"))
c4, c5, c6 = st.columns(3)
c4.metric("EPS (TTM)", help=appdata.term_help("EPS"), value="-" if val["eps_ttm"] is None else f"{val['eps_ttm']:,.0f}원")
c5.metric("BPS", help=appdata.term_help("BPS"), value="-" if val["bps"] is None else f"{val['bps']:,.0f}원")
c6.metric("시가총액", fmt_won(val["market_cap"], 1e12, "조") if val["market_cap"] and val["market_cap"] >= 1e12
          else fmt_won(val["market_cap"]))
if val["forward_per"]:
    st.metric(f"선행 PER ({fwd_year}E, 리포트 {int(cons.loc[fwd_year, '리포트 수'])}건 평균 EPS {fwd_eps:,.0f}원)",
              fmt_x(val["forward_per"]))
elif not appdata.public_mode():
    st.caption("선행 PER: '증권사 리포트' 페이지에서 추정 EPS를 등록하면 표시됩니다.")
st.caption("EPS·BPS는 지배주주 기준 ÷ 유통주식수(우선주 포함). 과거 실적 기준이라 업황 전환기에는 선행 PER과 함께 보세요.")

px_df = appdata.load_prices(comp["stock_code"])
if not px_df.empty:
    fig = px.line(px_df.reset_index(), x="Date", y="Close", height=240)
    fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), xaxis_title=None, yaxis_title="원")
    st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------- 실적
st.subheader("분기 실적")
recent = q.tail(8)
fig = go.Figure()
fig.add_bar(x=recent.index, y=recent["revenue"] / 1e8, name="매출(억)", marker_color="#9db4d6")
fig.add_bar(x=recent.index, y=recent["op"] / 1e8, name="영업이익(억)", marker_color="#2f5d9c")
fig.add_scatter(x=recent.index, y=recent["opm"] * 100, name="영업이익률(%)", yaxis="y2",
                mode="lines+markers", line=dict(color="#d9822b", width=3))
g_opm = industry.group_series(gts, group, "opm").reindex(recent.index) * 100
if g_opm.notna().any():
    fig.add_scatter(x=g_opm.index, y=g_opm.values, name=f"{group} 합산 영업이익률(%)", yaxis="y2",
                    mode="lines", line=dict(color="gray", dash="dot"))
fig.update_layout(barmode="group", height=360, margin=dict(l=10, r=10, t=30, b=10),
                  yaxis2=dict(overlaying="y", side="right", showgrid=False),
                  legend=dict(orientation="h", y=1.15))
st.plotly_chart(fig, width="stretch")

qt = pd.DataFrame({
    "매출(억)": recent["revenue"] / 1e8,
    "영업이익(억)": recent["op"] / 1e8,
    "영업이익률": recent["opm"],
    "매출 YoY": recent["rev_yoy"], "매출 QoQ": recent["rev_qoq"],
    "영업이익 YoY": recent["op_yoy"], "영업이익 QoQ": recent["op_qoq"],
    "지배주주순이익(억)": recent["ni_owner"] / 1e8,
}).T
pct_rows = ["영업이익률", "매출 YoY", "매출 QoQ", "영업이익 YoY", "영업이익 QoQ"]
st.dataframe(qt.style.format(lambda v: fmt_pct(v), subset=pd.IndexSlice[pct_rows, :])
             .format("{:,.0f}", subset=pd.IndexSlice[["매출(억)", "영업이익(억)", "지배주주순이익(억)"], :],
                     na_rep="-"), width="stretch")
st.caption("YoY/QoQ는 기준 시점이 적자이면 계산하지 않습니다(-). 분기 금액은 누적 공시값의 차이로 계산합니다.")

seg = d.get("segment")
bs_cfg = comp.get("business_segment")
if bs_cfg and seg is not None and not seg.empty:
    st.subheader(f"{bs_cfg['label']} 실적")
    st.caption("사업보고서·분기보고서의 '부문별 보고' 주석에서 추출. 산업 집계에는 이 부문 실적만 사용합니다.")
    s8 = seg.reindex(recent.index)
    share = s8["revenue"] / recent["revenue"]
    fig = go.Figure()
    fig.add_bar(x=s8.index, y=s8["revenue"] / 1e8, name=f"{bs_cfg['label']} 매출(억)", marker_color="#9db4d6")
    fig.add_bar(x=s8.index, y=s8["op"] / 1e8, name=f"{bs_cfg['label']} 영업이익(억)", marker_color="#2f5d9c")
    fig.add_scatter(x=s8.index, y=s8["op"] / recent["op"] * 100, name="회사 전체 영업이익 중 비중(%)",
                    yaxis="y2", mode="lines+markers", line=dict(color="#d9822b", width=3))
    fig.update_layout(barmode="group", height=320, margin=dict(l=10, r=10, t=30, b=10),
                      yaxis2=dict(overlaying="y", side="right", showgrid=False),
                      legend=dict(orientation="h", y=1.18))
    st.plotly_chart(fig, width="stretch")
    st.caption(f"최근 분기 매출 비중 {fmt_pct(share.iloc[-1])}, 영업이익률 {fmt_pct(s8['opm'].iloc[-1])}. "
               "부문 매출에는 부문 간 내부거래가 포함되어 있어 비중 합이 100%를 넘을 수 있습니다.")

with st.expander("연간 실적"):
    at = pd.DataFrame({
        "매출(억)": a["revenue"] / 1e8, "영업이익(억)": a["op"] / 1e8, "영업이익률": a["opm"],
        "매출 YoY": a["rev_yoy"], "영업이익 YoY": a["op_yoy"], "ROE": a["roe"], "부채비율": a["debt_ratio"],
    }).T
    at.columns = [str(c) for c in at.columns]
    prow = ["영업이익률", "매출 YoY", "영업이익 YoY", "ROE", "부채비율"]
    st.dataframe(at.style.format(lambda v: fmt_pct(v), subset=pd.IndexSlice[prow, :])
                 .format("{:,.0f}", subset=pd.IndexSlice[["매출(억)", "영업이익(억)"], :], na_rep="-"),
                 width="stretch")

# ---------------------------------------------------------------- 현금흐름
st.subheader("현금흐름 유형")
rows = []
for y, r in a.iterrows():
    t, desc, signs = classify(r["cfo"], r["cfi"], r["cff"])
    rows.append({"기간": str(y), "영업CF(억)": r["cfo"] / 1e8, "투자CF(억)": r["cfi"] / 1e8,
                 "재무CF(억)": r["cff"] / 1e8, "부호": signs, "유형": t, "해석": desc})
t, desc, signs = classify(last.get("cfo_ttm"), last.get("cfi_ttm"), last.get("cff_ttm"))
rows.append({"기간": f"TTM({q.index[-1]})", "영업CF(억)": last.get("cfo_ttm") / 1e8,
             "투자CF(억)": last.get("cfi_ttm") / 1e8, "재무CF(억)": last.get("cff_ttm") / 1e8,
             "부호": signs, "유형": t, "해석": desc})
cf = pd.DataFrame(rows)
st.markdown(f"**현재(TTM) 유형: {t}** — {desc}")
long = cf.melt(id_vars="기간", value_vars=["영업CF(억)", "투자CF(억)", "재무CF(억)"],
               var_name="구분", value_name="억원")
fig = px.bar(long, x="기간", y="억원", color="구분", barmode="group", height=300,
             color_discrete_sequence=["#2f5d9c", "#d9822b", "#6b8e6b"])
fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.15))
st.plotly_chart(fig, width="stretch")
st.dataframe(cf.style.format({c: "{:,.0f}" for c in ["영업CF(억)", "투자CF(억)", "재무CF(억)"]}, na_rep="-"),
             hide_index=True, width="stretch")

# ---------------------------------------------------------------- 재무 안정성
st.subheader("재무 안정성")
st.caption("반도체는 재고가 업황의 선행 신호입니다. 매출 대비 재고가 빠르게 늘면 수요 둔화 가능성을 의심해 보세요.")
bs = pd.DataFrame({
    "부채비율(%)": q["debt_ratio"] * 100,
    "재고자산/분기매출(배)": q["inventory"] / q["revenue"],
    "현금(억)": q["cash"] / 1e8,
    "CAPEX(억)": q["capex"] / 1e8,
}).tail(8)
cols = st.columns(2)
for i, col in enumerate(bs.columns):
    f = px.line(bs.reset_index(names="분기"), x="분기", y=col, markers=True, height=220, title=col)
    f.update_layout(margin=dict(l=10, r=10, t=40, b=10), xaxis_title=None, yaxis_title=None)
    cols[i % 2].plotly_chart(f, width="stretch")

# ---------------------------------------------------------------- 가동률 · 업황 민감도
st.subheader("가동률 (사업보고서)")
try:
    ut = appdata.utilization_info(comp["stock_code"], q.index[-1])
except DartError as e:
    ut = {"status": "없음", "note": str(e), "report_period": ""}
if ut["status"] == "ok":
    st.dataframe(pd.DataFrame(ut["items"], columns=["항목", "가동률(%)"]), hide_index=True, width="stretch",
                 column_config={"가동률(%)": st.column_config.NumberColumn(format="%.1f%%")})
    if any("시간 기준" in i for i, _ in ut["items"]):
        st.caption("'시간 기준' 가동률은 가동 가능 시간 대비 실제 가동 시간입니다. 24시간 멈추지 않는 반도체 공장은 거의 늘 100%라, "
                   "감산 여부는 이 숫자보다 업계 가동률지수나 웨이퍼 투입량(리포트)으로 보는 편이 낫습니다.")
elif ut["status"] == "미공시":
    st.info(f"공시하지 않음 — {ut['note']}")
else:
    st.caption(ut["note"])
eco_c = appdata.load_ecos(cfg)
ut_cfg = (cfg.get("ecos") or {}).get("utilization")
if eco_c is not None and ut_cfg and f"{ut_cfg['label']} 가동률" in eco_c:
    s = eco_c[f"{ut_cfg['label']} 가동률"].dropna()
    st.caption(f"{ut['report_period']} 보고서 기준 · 참고: 업계 {ut_cfg['label']} 가동률지수 {s.iloc[-1]:.1f} "
               f"({s.index[-1]:%Y-%m}, 2020=100). 소재·부품 회사에게는 이 지수가 '고객 가동률'입니다.")

st.subheader("업황 민감도")
drv = sensitivity.drivers(eco_c, cfg)
if not drv:
    st.caption("한국은행 지표(ECOS 키)가 있어야 계산할 수 있습니다.")
else:
    sres = [sensitivity.company(name, d, dn, dy) for dn, dy in drv.items()]
    st.dataframe(pd.DataFrame([{"업황 동인": r.driver, "판정": r.level, "β": r.beta, "상관계수 r": r.r,
                                "시차(분기)": r.lag, "표본": r.n} for r in sres]),
                 hide_index=True, width="stretch",
                 column_config={"β": st.column_config.NumberColumn(format="%.2f"),
                                "상관계수 r": st.column_config.NumberColumn(format="%.2f")})
    main = sres[0]
    if main.beta is not None and main.r is not None and main.r >= sensitivity.R_MID:
        st.markdown(f"{main.driver} 상승률이 10%p 더 높을 때 매출 성장률은 약 **{main.beta*10:+.1f}%p** 더 높은 경향입니다. "
                    "업황이 꺾이면 반대로 그만큼 빠질 수 있다는 뜻이기도 합니다.")
    st.caption("β = 동인 1%p당 매출 성장률 변화(%p), r = 관계의 일관성. 표본이 10개 안팎이라 참고용입니다. "
               "자세한 비교는 산업 대시보드 ⑤에서.")

# ---------------------------------------------------------------- 감사의견
st.subheader("감사의견")
client = appdata.dart()
aud = pd.DataFrame()
for y in (this_year - 1, this_year - 2):
    aud = client.audit_opinions(d["corp_code"], y)
    if not aud.empty:
        break
if aud.empty:
    st.info("감사의견 데이터를 찾지 못했습니다.")
else:
    rename = {"bsns_year": "사업연도", "adtor": "감사인", "adt_opinion": "감사의견",
              "adt_reprt_spcmnt_matter": "특기사항", "emphs_matter": "강조사항",
              "core_adt_matter": "핵심감사사항"}
    show = aud[[c for c in rename if c in aud.columns]].rename(columns=rename)
    st.dataframe(show, hide_index=True, width="stretch")
    opinions = " ".join(show.get("감사의견", pd.Series(dtype=str)).astype(str))
    if any(k in opinions for k in ("한정", "부적정", "거절")):
        st.error("⚠️ 변형의견이 있습니다. '사업·감사보고서' 페이지에서 근거를 확인하세요.")
    else:
        st.success("적정의견. 핵심감사사항(KAM)은 감사인이 특히 주의 깊게 본 계정이니 주석과 함께 확인하세요.")
