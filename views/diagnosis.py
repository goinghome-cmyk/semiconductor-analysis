import pandas as pd
import streamlit as st

from src import appdata, diagnosis, industry, sensitivity
from src.dart import DartError

cfg = appdata.current_industry()
comp = appdata.company_header("종합 진단", "📋")
name = comp["name"]
appdata.terms("diagnosis")
chain = cfg.get("value_chain", [])
leader = (cfg.get("lead_lag") or {}).get("leader") or (chain[0]["group"] if chain else None)

st.markdown("산업 흐름 · 성장 · 수익성 · 밸류에이션 · 현금흐름 · 이익의 질 · 감사 · 이상치를 **한 장**에 모았습니다. "
            "각 항목의 근거는 서로 다른 자료에서 오고, 누르면 자세한 페이지로 이동합니다.")

appdata.require_dart()
data = appdata.load_all(cfg)
snap = appdata.snapshot(cfg, data)
gts = industry.group_timeseries(cfg, data)
eco = appdata.load_ecos(cfg)
phase = industry.cycle_phase(gts, leader, industry.cycle_signals(gts, leader, eco, cfg))


def audit_of(corp_code: str) -> pd.DataFrame | None:
    try:
        return appdata.audit(corp_code)
    except DartError:
        return None


# ================================================================ 업계 매트릭스
drv = sensitivity.drivers(eco, cfg)
price_drv = next(iter(drv.items()), None)  # 첫 번째 동인 = 메모리 가격
all_items = {}
for c in cfg["companies"]:
    d = data.get(c["name"])
    if d and not d["quarterly"].empty:
        model = appdata.model_of(cfg, c["name"])
        orders, nn = None, None
        try:
            if model and model.get("backlog"):
                orders = appdata.order_info(c["stock_code"], d["quarterly"].index[-1])
            nn = appdata.note_numbers(c["stock_code"])
        except DartError:
            pass
        sens = sensitivity.company(c["name"], d, *price_drv) if price_drv else None
        all_items[c["name"]] = diagnosis.build(c["name"], cfg, data, snap, gts, phase, audit_of(d["corp_code"]),
                                               model, orders, sens, nn)

st.subheader("업계 한눈에 보기")
rows = []
for n, items in all_items.items():
    r = {"기업": n, "그룹": next(c["group"] for c in cfg["companies"] if c["name"] == n)}
    for it in items:
        r[it.area] = diagnosis.ICON[it.status]
    r["_bad"] = sum(it.status == diagnosis.BAD for it in items) * 2 + sum(it.status == diagnosis.WATCH for it in items)
    rows.append(r)
mat = pd.DataFrame(rows).sort_values("_bad").drop(columns="_bad").reset_index(drop=True)
appdata.pick_company_table(mat, key="diag_table")
st.caption("✅ 양호 · ⚠️ 주의 · 🔴 경고 · 🔵 참고 · ⚪ 판단불가. 확인할 것이 적은 순.")

# ================================================================ 선택 기업
st.divider()
items = all_items.get(name)
if not items:
    st.warning("데이터가 없습니다.")
    st.stop()

price, pdate = appdata.latest_price(comp["stock_code"])
st.header(name)
st.caption(f"{comp['group']} · {comp['segment']} · 최근 분기 {data[name]['quarterly'].index[-1]}"
           + (f" · {pdate} 종가 {price:,.0f}원" if price else ""))

cnt = {s: sum(it.status == s for it in items) for s in diagnosis.ICON}
c1, c2, c3 = st.columns(3)
c1.metric("✅ 양호", cnt[diagnosis.GOOD])
c2.metric("⚠️ 주의", cnt[diagnosis.WATCH])
c3.metric("🔴 경고", cnt[diagnosis.BAD])

for it in items:
    with st.container(border=True):
        a, b = st.columns([3, 1])
        a.markdown(f"{diagnosis.ICON[it.status]} **{it.area}** · {it.headline}")
        a.caption(it.evidence)
        b.page_link(it.page, label=f"{it.page_label} →")

st.caption("규칙 기반 체크리스트입니다. 투자 판단이 아니라 '어디를 더 볼지'를 정리한 것이며, 판정 기준은 각 페이지와 '지표 설명'에 있습니다.")
