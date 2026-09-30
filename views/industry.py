import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src import appdata, industry, reports, sensitivity
from src.appdata import fmt_pct, fmt_x
from src.llm import LLMError

cfg = appdata.current_industry()
chain = cfg.get("value_chain", [])
colors = {g["group"]: g["color"] for g in chain}
leader = (cfg.get("lead_lag") or {}).get("leader") or (chain[0]["group"] if chain else None)

st.title(f"🏭 {cfg['industry']} 산업 대시보드")
st.caption(cfg["description"])
appdata.terms("industry")
with st.expander("이 산업을 볼 때 주의할 점"):
    for n in cfg["notes"]:
        st.markdown(f"- {n}")

appdata.require_dart()
data = appdata.load_all(cfg)
if not data:
    st.warning("불러온 데이터가 없습니다.")
    st.stop()

snap = appdata.snapshot(cfg, data)
gts = industry.group_timeseries(cfg, data)
eco = appdata.load_ecos(cfg)
sigs = industry.cycle_signals(gts, leader, eco, cfg)
phase = industry.cycle_phase(gts, leader, sigs)


def chart_layout(fig, h=320, pct=False):
    fig.update_layout(height=h, margin=dict(l=10, r=10, t=30, b=10),
                      legend=dict(orientation="h", y=-0.25), xaxis_title=None)
    if pct:
        fig.update_yaxes(tickformat=".0%")
    return fig


# ================================================================ 1. 사이클 국면
st.header("① 지금 업황은 어느 국면인가")
ICON = {"개선": "🟢", "중립": "⚪", "악화": "🔴", "정보": "🔵"}
c1, c2 = st.columns([1, 2])
with c1:
    st.metric(f"{leader} 기준 사이클 국면", phase["phase"])
with c2:
    if "opm" in phase:
        st.markdown(
            f"**{industry.PHASES.get(phase['phase'], '개선·악화 신호가 비슷해 방향이 엇갈리는 구간')}**\n\n"
            f"- 수준: {leader} 영업이익률 **{phase['opm']*100:.1f}%** "
            f"(기간 {phase['period']} 중앙값 {phase['opm_median']*100:.1f}% → {'높음' if phase['level_high'] else '낮음'})\n"
            f"- 방향: 개선 신호 **{phase['up']}개**, 악화 신호 **{phase['down']}개** (판정 대상 {phase['n']}개)"
        )
with st.expander("판정 규칙 보기 (규칙 기반 추정이며 정답이 아닙니다)"):
    st.markdown(
        "- **수준**: 칩메이커 합산 영업이익률이 조회 기간 중앙값 이상이면 '높음'\n"
        "- **방향**: 아래 신호 중 (개선 수 − 악화 수)가 양수면 좋아지는 중, 음수면 나빠지는 중\n"
        "- **국면**: 높음+개선=확장 · 높음+악화=둔화 · 낮음+개선=회복 · 낮음+악화=침체 · 동점이면 전환 구간\n"
        "- 조회 기간이 약 3년이라 '수준'의 기준이 짧습니다. 사이클 한 바퀴(보통 3~4년)를 다 담지 못할 수 있습니다."
    )

cols = st.columns(2)
for i, s in enumerate(sigs):
    with cols[i % 2].container(border=True):
        st.markdown(f"{ICON[s.status]} **{s.name}** · {s.status}")
        st.markdown(s.value)
        st.caption(f"{s.meaning}\n\n👀 전환 신호: {s.watch}\n\n규칙: {s.rule}")

# ================================================================ 2. 선행지표
if eco is not None and not eco.empty:
    st.header("② 선행지표 (한국은행 ECOS)")
    st.caption("분기 실적은 늦게 나옵니다. 월별 가격·재고 지표로 방향을 먼저 확인합니다.")
    price_cols = [p["name"] for p in cfg["ecos"].get("price", []) if p["name"] in eco]
    if price_cols:
        pdf = eco[price_cols].dropna(how="all").reset_index(names="월").melt("월", var_name="지표", value_name="지수")
        fig = px.line(pdf, x="월", y="지수", color="지표", title="메모리 수출물가지수 (계약통화 기준, 2020=100)")
        st.plotly_chart(chart_layout(fig), width="stretch")
    if {"출하", "재고"} <= set(eco.columns):
        yo = pd.DataFrame({k: eco[k].rolling(3).mean() / eco[k].rolling(3).mean().shift(12) - 1
                           for k in ("출하", "재고")}).dropna()
        fig = go.Figure()
        fig.add_scatter(x=yo.index, y=yo["출하"], name="출하 YoY", line=dict(color="#2f5d9c", width=3))
        fig.add_scatter(x=yo.index, y=yo["재고"], name="재고 YoY", line=dict(color="#c0392b", width=3))
        fig.update_layout(title=f"출하-재고 순환 ({cfg['ecos']['cycle']['label']}, 3개월 평균)")
        st.plotly_chart(chart_layout(fig, pct=True), width="stretch")
        st.caption("파란선(출하)이 빨간선(재고) 위에 있으면 재고가 소진되는 중(호황 쪽), 아래면 재고가 쌓이는 중(둔화 쪽)입니다. "
                   "반도체만이 아닌 전자부품 업종 전체 지수입니다.")
    ut = cfg["ecos"].get("utilization")
    if ut and f"{ut['label']} 가동률" in eco:
        rc, cc = f"{ut['label']} 가동률", f"{ut['label']} 생산능력"
        u = eco[[rc, cc]].dropna(how="all")
        fig = go.Figure()
        fig.add_scatter(x=u.index, y=u[rc], name="가동률지수(계절조정)", line=dict(color="#2f5d9c", width=3))
        fig.add_scatter(x=u.index, y=u[cc], name="생산능력지수", line=dict(color="#6b8e6b", width=3, dash="dot"))
        fig.update_layout(title=f"{ut['label']} 가동률·생산능력 (2020=100)")
        st.plotly_chart(chart_layout(fig), width="stretch")
        st.caption("가동률 = 공장이 얼마나 바쁘게 도는지(소재·부품 회사에게는 '고객 가동률'), 생산능력 = 설비 규모. "
                   "생산능력이 늘면서 가동률이 내려가면 새 설비를 아직 다 채우지 못한 상태입니다.")
    fx_name = (cfg["ecos"].get("fx") or {}).get("name")
    if fx_name in eco:
        with st.expander(fx_name):
            fig = px.line(eco[[fx_name]].dropna().reset_index(names="월"), x="월", y=fx_name)
            st.plotly_chart(chart_layout(fig, 260), width="stretch")
            st.caption("원화 약세(환율 상승)는 수출 기업의 원화 환산 매출을 늘립니다.")

# ================================================================ 3. 밸류체인
st.header("③ 호황·불황이 밸류체인에 어떻게 퍼지나")
if chain:
    st.markdown("  →  ".join(f"**{g['group']}**" for g in chain))
    st.caption(" · ".join(f"{g['group']}: {g['role']}" for g in chain))
g = gts[gts["분기"] >= gts["분기"].min()].copy()
t1, t2 = st.tabs(["그룹별 매출 YoY", "그룹별 영업이익률"])
with t1:
    fig = px.line(g.dropna(subset=["revenue_yoy"]), x="분기", y="revenue_yoy", color="그룹",
                  markers=True, color_discrete_map=colors, labels={"revenue_yoy": "매출 YoY"})
    st.plotly_chart(chart_layout(fig, pct=True), width="stretch")
with t2:
    fig = px.line(g, x="분기", y="opm", color="그룹", markers=True, color_discrete_map=colors,
                  labels={"opm": "영업이익률"})
    st.plotly_chart(chart_layout(fig, pct=True), width="stretch")

ll = cfg.get("lead_lag")
if ll:
    st.subheader(f"{ll['leader']} 설비투자 → {ll['follower']} 매출")
    st.caption(ll["note"])
    lag = st.slider("시차 (분기)", 0, 4, 2, help="선행 지표를 몇 분기 뒤로 밀어서 비교할지")
    ldf = industry.lead_lag(gts, cfg, lag).dropna(subset=[f"선행({lag}분기 전)", "후행"], how="all")
    fig = go.Figure()
    fig.add_bar(x=ldf.index, y=ldf["후행"], name=f"{ll['follower']} 매출 YoY",
                marker_color=colors.get(ll["follower"], "#d9822b"))
    fig.add_scatter(x=ldf.index, y=ldf[f"선행({lag}분기 전)"],
                    name=f"{ll['leader']} CAPEX(4분기 합) YoY, {lag}분기 전 값",
                    line=dict(color=colors.get(ll["leader"], "#2f5d9c"), width=3))
    st.plotly_chart(chart_layout(fig, pct=True), width="stretch")
    both = ldf.dropna(subset=[f"선행({lag}분기 전)", "후행"])
    lead_now = industry.group_series(gts, ll["leader"], "capex_ttm_yoy").dropna()
    msg = (f"겹치는 기간이 {len(both)}개 분기뿐이라 상관관계를 계산하기엔 표본이 부족합니다. "
           "그래프로 방향만 참고하세요.")
    if len(lead_now):
        msg += (f" 최근 {ll['leader']} 설비투자는 전년 대비 **{lead_now.iloc[-1]*100:+.1f}%** → "
                f"이 경향이 이어진다면 향후 {ll['follower']} 매출에 "
                f"{'우호적' if lead_now.iloc[-1] > 0 else '부담'}일 수 있습니다.")
    st.markdown(msg)

# ================================================================ 4. 이익 집중도
st.header("④ 누가 돈을 버는가")
pool = industry.profit_pool(cfg, data)
if not pool.empty:
    top2 = pool["이익 비중"].head(2).sum()
    h = industry.hhi(pool["매출 비중"])
    c1, c2 = st.columns(2)
    c1.metric("상위 2개사 영업이익 비중", fmt_pct(top2), f"{', '.join(pool['기업'].head(2))}", delta_color="off", delta_arrow="off")
    c2.metric("매출 집중도 (HHI)", f"{h:,.0f}", "고집중" if h >= 2500 else ("중간" if h >= 1500 else "경쟁적"),
              delta_color="off", delta_arrow="off")
    st.caption("최근 4개 분기 기준, 이 앱이 다루는 기업들 안에서의 비중입니다(시장 전체 점유율 아님). "
               "HHI는 2,500 이상이면 고집중으로 봅니다. 삼성전자는 반도체(DS) 부문만 포함.")
    fig = px.bar(pool, x="기업", y="이익 비중", color="그룹", color_discrete_map=colors, text_auto=".1%")
    st.plotly_chart(chart_layout(fig, 300, pct=True), width="stretch")

    gp = pool.groupby("그룹")[["매출(TTM)", "영업이익(TTM)"]].sum()
    gp["영업이익률"] = gp["영업이익(TTM)"] / gp["매출(TTM)"]
    gp["이익 비중"] = pool.groupby("그룹")["이익 비중"].sum()
    last_yoy = gts.dropna(subset=["revenue_yoy"]).groupby("그룹").last()["revenue_yoy"]
    gp["매출 YoY(최근 분기)"] = last_yoy
    for col in ("PER", "PBR", "ROE"):
        gp[f"{col} 평균"] = snap.groupby("그룹")[col].mean()
    gp[["매출(TTM)", "영업이익(TTM)"]] /= 1e8
    gp = gp.rename(columns={"매출(TTM)": "매출(TTM,억)", "영업이익(TTM)": "영업이익(TTM,억)"})
    gp = gp.reindex([c["group"] for c in chain if c["group"] in gp.index])
    st.subheader("밸류체인 그룹별 비교")
    st.dataframe(gp.style.format({
        "매출(TTM,억)": "{:,.0f}", "영업이익(TTM,억)": "{:,.0f}", "영업이익률": fmt_pct,
        "이익 비중": fmt_pct, "매출 YoY(최근 분기)": fmt_pct, "PER 평균": fmt_x,
        "PBR 평균": fmt_x, "ROE 평균": fmt_pct}, na_rep="-"), width="stretch")
    st.caption("밸류에이션(PER·PBR·ROE)은 회사 전체 기준입니다. 이익이 몰린 그룹과 밸류에이션이 높은 그룹이 다르면, "
               "시장이 '다음 수혜'를 먼저 반영하고 있을 수 있습니다.")

# ================================================================ 5. 업황 민감도
st.header("⑤ 누가 업황에 민감한가")
st.markdown("업황 동인(메모리 가격, 반도체 가동률)이 변할 때 **각 기업의 매출 성장률이 얼마나 따라 움직였는지**입니다. "
            "업황이 꺾일 때 누가 가장 크게 흔들릴지 가늠할 수 있습니다.")
sens_tab, sens_res = sensitivity.table(cfg, data, eco)
if sens_tab.empty:
    st.info("한국은행 지표(ECOS 키)가 있어야 계산할 수 있습니다.")
else:
    drv = st.radio("업황 동인", list(sens_tab["동인"].unique()), horizontal=True)
    t = sens_tab[sens_tab["동인"] == drv].drop(columns="동인")
    t = t.merge(snap[["기업", "PER", "PBR"]], on="기업", how="left").reset_index(drop=True)
    appdata.pick_company_table(
        t, key="sens_table",
        column_config={"β (동인 1%p당 매출 %p)": st.column_config.NumberColumn(format="%.2f"),
                       "상관계수 r": st.column_config.NumberColumn(format="%.2f"),
                       "PER": st.column_config.NumberColumn(format="%.1f배"),
                       "PBR": st.column_config.NumberColumn(format="%.1f배")})
    cur = st.session_state["company"]
    r = sens_res.get((cur, drv))
    if r is not None and not r.points.empty:
        fig = px.scatter(r.points, x="동인 YoY", y="매출 YoY", text="분기", height=340)
        xs = np.linspace(r.points["동인 YoY"].min(), r.points["동인 YoY"].max(), 20)
        fig.add_scatter(x=xs, y=r.alpha + r.beta * xs, mode="lines", name="추세선",
                        line=dict(color="gray", dash="dash"))
        fig.update_traces(textposition="top center", selector=dict(mode="markers+text"))
        fig.update_layout(title=f"{cur}: {drv} vs 매출 성장률 (시차 {r.lag}분기)", showlegend=False)
        fig.update_xaxes(tickformat=".0%")
        st.plotly_chart(chart_layout(fig, 340, pct=True), width="stretch")
        st.markdown(f"**{cur}** — {r.level}. {drv} 상승률이 10%p 더 높을 때 매출 성장률은 약 **{r.beta*10:+.1f}%p** "
                    f"더 높은 경향 (상관계수 {r.r:.2f}, 표본 {r.n}개 분기).")
    with st.expander("읽는 법과 한계"):
        st.markdown(
            "- **β(기울기)**: 동인이 1%p 오를 때 매출 성장률이 몇 %p 오르는지. 클수록 업황에 크게 흔들립니다.\n"
            "- **r(상관계수)**: 관계가 얼마나 일관적인지. 0.7 이상 '뚜렷', 0.4~0.7 '약하게', 그 미만 '불명확'.\n"
            "- **시차**: 동인이 매출보다 몇 분기 먼저 움직였을 때 더 잘 맞는지 (0 또는 1분기 중 선택).\n"
            "- 기업 데이터가 약 3년이라 **표본이 10개 안팎**입니다. 한 번의 호황이 결과를 좌우할 수 있어, 방향을 가늠하는 참고용입니다.\n"
            "- 이번 호황은 물량보다 **가격**이 이끌었습니다. 가동률과의 관계가 '반대 방향'으로 나오는 것은 "
            "생산능력이 늘면서 가동률지수가 내려간 영향일 수 있고, 표본이 적어 우연일 가능성도 큽니다.\n"
            "- 삼성전자는 반도체(DS) 부문 매출 기준입니다.")

# ================================================================ 6. 기업별 상세
st.header("⑥ 기업별 상세 비교")
st.dataframe(appdata.style_snapshot(snap), hide_index=True, width="stretch")
tab1, tab2, tab3 = st.tabs(["PBR-ROE", "영업이익률 추이", "현금흐름 유형"])
with tab1:
    sc = snap.dropna(subset=["PBR", "ROE"]).copy()
    if sc.empty:
        st.info("주가 또는 자본 데이터가 부족합니다.")
    else:
        sc["ROE(%)"] = sc["ROE"] * 100
        sc["size"] = sc["시가총액(억)"].fillna(0).clip(lower=1) ** 0.5
        fig = px.scatter(sc, x="ROE(%)", y="PBR", text="기업", size="size", color="그룹",
                         color_discrete_map=colors, height=420, size_max=45)
        fig.update_traces(textposition="top center")
        if len(sc) >= 3:
            slope, icpt = np.polyfit(sc["ROE(%)"], sc["PBR"], 1)
            xs = np.linspace(sc["ROE(%)"].min(), sc["ROE(%)"].max(), 20)
            fig.add_scatter(x=xs, y=slope * xs + icpt, mode="lines", name="추세선",
                            line=dict(color="gray", dash="dash"))
        fig.update_layout(margin=dict(l=10, r=10, t=30, b=10), showlegend=False)
        st.plotly_chart(fig, width="stretch")
        st.caption("ROE(수익성)가 높을수록 PBR도 높은 경향이 있습니다. 점선(추세선)보다 아래에 있으면 같은 ROE 대비 PBR이 "
                   "업계보다 낮게, 위에 있으면 높게 형성되어 있다는 뜻입니다. 그 이유(성장 기대, 위험, 일회성 이익 등)는 따로 확인해야 합니다.")
with tab2:
    rows = []
    for name, d in data.items():
        for period, v in d["quarterly"]["opm"].items():
            rows.append({"분기": period, "기업": name, "영업이익률": v})
    fig = px.line(pd.DataFrame(rows), x="분기", y="영업이익률", color="기업", markers=True, height=420)
    st.plotly_chart(chart_layout(fig, 420, pct=True), width="stretch")
    st.caption("회사 전체 기준 영업이익률입니다(삼성전자 포함).")
with tab3:
    cnt = snap["현금흐름 유형(TTM)"].value_counts().rename_axis("유형").reset_index(name="기업 수")
    st.plotly_chart(px.bar(cnt, x="유형", y="기업 수", height=280), width="stretch")
    st.dataframe(snap[["기업", "그룹", "현금흐름 유형(TTM)"]], hide_index=True, width="stretch")
    st.caption("최근 4개 분기(TTM) 합산 현금흐름 기준. 분류 기준은 '지표 설명' 페이지 참고.")

# ================================================================ AI 브리핑
llm = appdata.llm()
if llm is None:
    if not appdata.public_mode():
        st.header("🤖 AI 산업 브리핑")
        st.info("Anthropic API 키를 넣으면 위 분석·리포트 전망·최근 뉴스를 종합한 브리핑을 만들 수 있습니다.")
else:
    st.header("🤖 AI 산업 브리핑")
    # 공개 모드에서는 업로드된 리포트(저작권 자료)를 브리핑 재료로 쓰지 않는다
    notes = [] if appdata.public_mode() else reports.load_report_notes()
    outlooks = [f"- [{n.get('broker')} {n.get('report_date')}] {n.get('industry_outlook')}"
                for n in notes if n.get("industry_outlook")]
    try:
        headlines = appdata.news_search(cfg["news_keywords"][0], 14)["제목"].head(25).tolist()
    except Exception:
        headlines = []
    if st.button("브리핑 생성", type="primary"):
        sig_txt = "\n".join(f"- {s.name}: {s.status} ({s.value})" for s in sigs)
        doc = (
            f"# 사이클 판정: {phase['phase']}\n{sig_txt}\n\n"
            f"# 밸류체인 그룹별 분기 지표\n{gts[['분기', '그룹', 'revenue_yoy', 'opm', 'capex_ttm_yoy']].to_csv(index=False)}\n\n"
            f"# 이익 비중\n{pool.to_csv(index=False)}\n\n"
            f"# 기업별 최신 지표\n{snap.to_csv(index=False)}\n\n"
            f"# 증권사 리포트 산업 전망\n" + ("\n".join(outlooks) or "(없음)") + "\n\n"
            f"# 최근 2주 뉴스 제목\n" + ("\n".join(f"- {h}" for h in headlines) or "(없음)")
        )
        instruction = (
            f"{cfg['industry']} 산업 브리핑을 작성하세요. 구성: 1) 사이클 국면 판단과 근거(규칙 판정에 동의/반대 의견 포함) "
            "2) 밸류체인 중 호황이 어디에 있고 어디로 옮겨갈지 3) 이익 집중과 밸류에이션의 관계 "
            "4) 리포트·뉴스가 말하는 향후 변수 5) 앞으로 확인할 지표. 마크다운으로 간결하게."
        )
        with st.spinner("브리핑 작성 중…"):
            try:
                text, _ = llm.ask(instruction, doc)
                st.markdown(text)
                appdata.ai_disclaimer()
            except LLMError as e:
                st.error(str(e))
