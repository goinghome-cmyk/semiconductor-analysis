import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src import appdata, docrender, quality
from src.dart import DartError

cfg = appdata.current_industry()
comp = appdata.company_header("이익의 질", "🧪")
name = comp["name"]
appdata.terms("quality")

st.markdown(
    "손익계산서의 **이익이 믿을 만한지** 다른 자료로 교차 확인합니다. "
    "현금흐름표(현금으로 들어왔나) · 재무상태표(재고·외상이 쌓이나) · 감사보고서(감사인이 어디를 걱정했나) · "
    "주석(재고·외상을 얼마나 보수적으로 평가했나)을 한 줄로 잇습니다."
)

appdata.require_dart()
data = appdata.load_all(cfg)

# ================================================================ 업계 비교표
st.subheader("업계 한눈에 보기")
rows, per_company = [], {}
with st.spinner("감사의견·주석을 불러오는 중…"):
    for c in cfg["companies"]:
        d = data.get(c["name"])
        if not d or d["quarterly"].empty:
            continue
        model = appdata.model_of(cfg, c["name"])
        orders, nn = None, None
        try:
            if model and model.get("backlog"):
                orders = appdata.order_info(c["stock_code"], d["quarterly"].index[-1])
            au = quality.audit_summary(appdata.audit(d["corp_code"]))
            nn = appdata.note_numbers(c["stock_code"])
        except DartError:
            au = {"available": False}
        cs = quality.checks(d, model, orders, nn)
        per_company[c["name"]] = (cs, au, model)
        row = {"기업": c["name"], "그룹": c["group"], "사업 형태": (model or {}).get("name", "-")}
        for x in cs:
            # 열 이름은 기업마다 붙는 '(핵심)' 같은 꼬리표를 떼고 통일
            row[x.name.split(" (")[0]] = quality.ICON[x.status]
        row["감사의견"] = ("🔴 " if au.get("modified") else "✅ ") + ", ".join(au.get("opinions", [])) \
            if au.get("available") else "-"
        row["핵심감사사항"] = " · ".join(sorted({k["topic"] for k in au.get("kams", [])})) or "-"
        row["_score"] = sum(x.status == "경고" for x in cs) * 2 + sum(x.status == "주의" for x in cs)
        rows.append(row)
table = pd.DataFrame(rows).sort_values("_score", ascending=False).drop(columns="_score").reset_index(drop=True)
appdata.pick_company_table(table, key="quality_table")
st.caption("✅ 양호 · ⚠️ 주의 · 🔴 경고 · 🔵 참고 · ⚪ 판단불가. 경고·주의가 많은 순. 기준은 아래 각 항목의 '규칙'에 있습니다.")

# ================================================================ 선택 기업
st.divider()
st.header(f"{name}")
if name not in per_company:
    st.warning("데이터가 없습니다.")
    st.stop()
cs, au, model = per_company[name]
d = data[name]
q = d["quarterly"]
by_key = {x.key: x for x in cs}

if model:
    st.info(f"**사업 형태: {model['name']}** — {model['desc']}\n\n이 형태에서 중요한 지표: {model['watch']}")

# ---------------------------------------------------------------- 교차 확인 결론
cross = []
inv_chk = by_key.get("inventory")
if inv_chk and "수주 대비" in inv_chk.name and inv_chk.status == "정보":
    cross.append(f"재고가 늘었지만 **선수금·수주잔고도 함께 늘었습니다** ({inv_chk.value.split(' · ', 1)[-1]}). "
                 "수주산업에서는 받은 주문을 만드는 중이라는 뜻에 가깝습니다.")
for k in au.get("kams", []):
    for chk in quality.kam_checks(k, by_key):
        if chk.status in ("주의", "경고"):
            cross.append(f"감사인이 '**{k['text']}**'을(를) 핵심감사사항으로 꼽았고, 관련 수치도 "
                         f"{quality.ICON[chk.status]} {chk.status}입니다 → {chk.name}: {chk.value}")
# 재무상태표(일수) × 주석(충당금): 쌓이는데 충당금은 그대로인가, 충당금이 이익을 키웠나
for days_key, allow_key, what, down_msg in (
    ("inventory", "inv_allow", "재고",
     "재고 일수는 늘었는데 평가충당금 비율은 오르지 않았습니다. 쌓인 재고를 제값에 팔 수 있다고 보고 있다는 뜻이라, "
     "업황이 꺾이면 뒤늦게 평가손실이 한꺼번에 나올 수 있습니다."),
    ("receivables", "rec_allow", "매출채권",
     "매출채권 회수일수는 늘었는데 손실충당금 비율은 오르지 않았습니다. 늦어진 외상을 모두 받을 수 있다고 보고 있다는 뜻입니다."),
):
    dc, ac = by_key.get(days_key), by_key.get(allow_key)
    delta = ac.data.get("delta") if ac else None
    if not dc or dc.status not in ("주의", "경고") or delta is None:
        continue
    if delta <= 0 and dc.status == "경고":  # 조금 늘어난 정도로는 충당금까지 볼 일이 아님
        cross.append(down_msg)
    elif ac.status == "주의":
        cross.append(f"{what} 일수가 늘었고 주석의 충당금 비율도 올랐습니다. 회사도 {what}의 가치가 떨어지고 있다고 "
                     f"인정한 것이라, 숫자 이상이 실제 손실로 이어지고 있습니다 ({ac.value}).")
ia = by_key.get("inv_allow")
if ia and ia.status == "정보" and ia.data.get("released", 0) > 0:
    cross.append("재고 평가충당금이 크게 줄었습니다. 과거 불황기에 깎아둔 재고를 팔거나 값이 회복되면 그 손실이 "
                 "**이익으로 되돌아오므로**, 최근 이익률에는 반복되지 않는 부분이 섞여 있을 수 있습니다.")
no = by_key.get("nonop")
if no and no.status == "주의":
    cross.append(f"이익의 상당 부분이 본업 밖에서 나왔습니다 ({no.value.split(' → ')[-1]}). "
                 "영업이익 기준 밸류에이션과 순이익 기준 PER이 다르게 보일 수 있습니다.")
tied = [lbl for k, lbl in (("inventory", "재고"), ("receivables", "매출채권"))
        if by_key.get(k) and by_key[k].status in ("주의", "경고")]
if by_key.get("cash") and by_key["cash"].status in ("주의", "경고") and tied:
    what = "·".join(tied)
    where = {"재고": "재고", "매출채권": "외상(매출채권)"}
    cross.append(f"이익이 현금으로 덜 들어오는 동시에 {what} 일수도 늘었습니다. "
                 f"이익 중 일부가 아직 **{' 또는 '.join(where[t] for t in tied)}(으)로 묶여 있다**는 뜻입니다.")
if by_key.get("restated") and by_key["restated"].status == "주의":
    cross.append("과거 실적이 재작성된 분기가 있습니다. 전년 대비 비교가 왜곡될 수 있으니 "
                 "'이상치 추적' 페이지에서 원인을 확인하세요.")
with st.container(border=True):
    st.markdown("**🔗 자료를 엮어서 보이는 것**")
    if cross:
        for c_ in cross:
            st.markdown(f"- {c_}")
    else:
        st.markdown("- 감사인이 주목한 계정과 재무 수치 사이에 겹치는 경고 신호는 없습니다.")
    st.caption("규칙 기반 연결입니다. 경고가 곧 문제라는 뜻은 아니며, 원문(주석·감사보고서)으로 확인할 지점을 알려줍니다.")

# ---------------------------------------------------------------- 점검 항목
st.subheader("점검 항목")
cols = st.columns(2)
for i, x in enumerate(cs):
    with cols[i % 2].container(border=True):
        st.markdown(f"{quality.ICON[x.status]} **{x.name}** · {x.status}")
        st.markdown(x.value)
        if x.meaning:
            st.caption(x.meaning + (f"\n\n규칙: {x.rule}" if x.rule else ""))

t1, t2 = st.tabs(["이익 vs 현금", "재고·매출채권 일수"])
with t1:
    fig = go.Figure()
    fig.add_bar(x=q.index, y=q["ni_ttm"] / 1e8, name="순이익(최근 4분기, 억)", marker_color="#9db4d6")
    fig.add_bar(x=q.index, y=q["cfo_ttm"] / 1e8, name="영업현금흐름(최근 4분기, 억)", marker_color="#2f5d9c")
    fig.update_layout(barmode="group", height=300, margin=dict(l=10, r=10, t=10, b=10),
                      legend=dict(orientation="h", y=-0.25))
    st.plotly_chart(fig, width="stretch")
    st.caption("진한 막대(현금)가 옅은 막대(이익)보다 꾸준히 작으면, 이익이 현금보다 앞서 있다는 신호입니다.")
with t2:
    days = pd.DataFrame({
        "재고 보유일수": q["inventory"] / q["revenue"] * 91,
        "매출채권 회수일수": q["receivables"] / q["revenue"] * 91,
    }).where(q["revenue"] > 0)
    fig = go.Figure()
    for col, color in (("재고 보유일수", "#d9822b"), ("매출채권 회수일수", "#6b8e6b")):
        fig.add_scatter(x=days.index, y=days[col], name=col, mode="lines+markers", line=dict(color=color, width=3))
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="일",
                      legend=dict(orientation="h", y=-0.25))
    st.plotly_chart(fig, width="stretch")

# ---------------------------------------------------------------- 감사
st.subheader("감사인은 어디를 봤나")
if not au.get("available"):
    st.info("감사의견 데이터를 찾지 못했습니다.")
else:
    hist = " → ".join(f"{a}({p})" for p, a in reversed(au["auditors"]))
    st.markdown(
        f"- 감사의견: {'🔴' if au['modified'] else '✅'} **{', '.join(au['opinions'])}**"
        f"{' (변형의견: 근거를 반드시 확인하세요)' if au['modified'] else ''}\n"
        f"- 감사인: {hist} · 교체 **{'있음' if au['auditor_changed'] else '없음'}**"
    )
    if au["auditor_changed"]:
        st.caption("감사인 교체 자체는 흔하지만(주기적 지정제 등), 교체 직후에는 회계 판단이 달라질 수 있어 핵심감사사항과 함께 봅니다.")

    notes = None
    for i, k in enumerate(au["kams"]):
        with st.container(border=True):
            st.markdown(f"**🔍 핵심감사사항 {i + 1}: {k['text']}**")
            st.caption(f"주제: {k['topic']}")
            for chk in quality.kam_checks(k, by_key):
                st.markdown(f"관련 수치 → {quality.ICON[chk.status]} **{chk.name}**: {chk.value}")
            if k["notes"] and st.toggle("관련 주석 보기", key=f"kam_note_{i}"):
                if notes is None:
                    with st.spinner("사업보고서 주석을 불러오는 중…"):
                        try:
                            notes = appdata.note_blocks(comp["stock_code"])
                        except DartError as e:
                            st.error(str(e))
                            notes = []
                hit = appdata.find_note(notes, k["notes"])
                if hit:
                    with st.container(height=420):
                        st.markdown(docrender.CSS + docrender.to_html(hit[1], hit[0]), unsafe_allow_html=True)
                else:
                    st.caption("관련 주석을 찾지 못했습니다. '사업·감사보고서' 페이지에서 직접 찾아보세요.")
    if not au["kams"]:
        st.caption("보고된 핵심감사사항이 없습니다.")
