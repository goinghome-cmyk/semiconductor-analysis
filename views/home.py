import streamlit as st

from src import appdata, industry, quality, summary
from src.dart import DartError

cfg = appdata.current_industry()
chain = cfg.get("value_chain", [])
leader = (cfg.get("lead_lag") or {}).get("leader") or (chain[0]["group"] if chain else None)

st.title(f"📊 {cfg['industry']} 산업 분석")
st.markdown(
    f"국내 상장 {cfg['industry']} 기업 {len(cfg['companies'])}곳의 **공시·재무제표·한국은행 통계·뉴스**를 엮어서, "
    "**업황이 어디쯤인지**와 **각 기업의 숫자를 믿을 수 있는지**를 함께 보는 도구입니다."
)

appdata.require_dart()
data = appdata.load_all(cfg)
if not data:
    st.warning("불러온 데이터가 없습니다.")
    st.stop()
eco = appdata.load_ecos(cfg)
gts = industry.group_timeseries(cfg, data)
sigs = industry.cycle_signals(gts, leader, eco, cfg)
phase = industry.cycle_phase(gts, leader, sigs)
pool = industry.profit_pool(cfg, data)

q_by = {}
with st.spinner("기업별 점검 중…"):
    for c in cfg["companies"]:
        d = data.get(c["name"])
        if not d or d["quarterly"].empty:
            continue
        model = appdata.model_of(cfg, c["name"])
        orders, nn = None, None
        try:
            if model and model.get("backlog"):
                orders = appdata.order_info(c["stock_code"], d["quarterly"].index[-1])
            nn = appdata.note_numbers(c["stock_code"])
        except DartError:
            pass
        q_by[c["name"]] = quality.checks(d, model, orders, nn)

_, price_date = appdata.latest_price(cfg["companies"][0]["stock_code"])
st.caption(f"🗓️ 데이터 기준: {summary.data_asof(data, eco, price_date)}")

# ---------------------------------------------------------------- 지금 한눈에
st.subheader("지금 한눈에")
with st.container(border=True):
    for f in summary.facts(cfg, data, gts, eco, sigs, phase, pool, q_by):
        st.markdown(f"- {f}")
    st.caption("숫자는 공시·통계에서 그대로 계산했고, '국면'·'경고' 같은 판정은 각 페이지에 공개된 규칙을 따른 결과입니다.")

# ---------------------------------------------------------------- 어디부터 볼까
st.subheader("어디부터 볼까요?")
guides = [
    ("views/industry.py", "🏭 산업 대시보드", "업황 국면 신호등, 메모리 가격·가동률 같은 선행지표, 호황이 밸류체인에 퍼지는 모습, 누가 업황에 민감한지"),
    ("views/diagnosis.py", "📋 종합 진단", "기업 하나를 8~9개 항목 체크리스트 한 장으로. 항목마다 근거와 자세히 볼 페이지를 연결합니다"),
    ("views/quality.py", "🧪 이익의 질", "이익이 현금으로 들어오는지, 재고·외상이 쌓이는지, 감사인이 어디를 걱정했는지를 교차 확인"),
    ("views/anomaly.py", "🔍 이상치 추적", "숫자가 갑자기 변한 분기를 찾고, 그 분기 보고서·공시·뉴스에서 이유를 찾습니다"),
    ("views/filings.py", "📑 사업·감사보고서", "사업의 내용, 경영진단, 감사의견, 재무제표 주석 원문을 읽기 쉽게"),
    ("views/guide.py", "📘 지표 설명", "모든 판정 기준과 계산식, 반도체 사업 형태별 차이"),
]
cols = st.columns(2)
for i, (page, label, desc) in enumerate(guides):
    with cols[i % 2].container(border=True):
        st.page_link(page, label=label)
        st.caption(desc)

with st.expander("이 앱이 자료를 엮는 방식 (예시)"):
    st.markdown(
        "- **재무제표 × 감사보고서**: 감사인이 핵심감사사항으로 꼽은 계정(예: 장비매출의 발생사실)과 "
        "관련 수치(매출채권 회수일수)가 동시에 나빠지면 함께 표시합니다.\n"
        "- **재무제표 × 공시 × 뉴스**: 분기 실적이 재작성되면 그 분기 보고서 원문, 같은 시기 분할·매각 공시와 기사를 모아 이유를 추적합니다.\n"
        "- **한국은행 통계 × 기업 실적**: 메모리 가격·반도체 가동률 변화에 각 기업 매출이 얼마나 따라 움직였는지 계산합니다.\n"
        "- **사업 형태 × 재무제표**: 수주산업(장비)은 재고가 늘어도 선수금·수주잔고가 함께 늘면 '주문 대응'으로 봅니다."
    )
st.caption("👈 왼쪽 위 메뉴(휴대폰은 ☰ 또는 » 버튼)에서 산업과 기업을 바꿀 수 있습니다. 표의 행을 눌러도 기업이 바뀝니다.")
