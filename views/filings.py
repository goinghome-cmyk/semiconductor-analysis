import streamlit as st

from src import appdata, disclosure, docrender
from src.dart import DartError
from src.llm import LLMError

cfg = appdata.current_industry()
comp = appdata.company_header("사업·감사보고서", "📑")
name = comp["name"]
appdata.terms("filings")

appdata.require_dart()

with st.spinner("최근 사업보고서 원문을 불러오는 중…"):
    try:
        rep = appdata.business_report(comp["stock_code"])
    except DartError as e:
        st.error(str(e))
        st.stop()
if not rep or not rep["sections"]:
    st.warning("최근 사업보고서를 찾지 못했거나 원문을 해석하지 못했습니다.")
    st.stop()

st.caption(f"{rep['report_nm']} · 접수일 {rep['rcept_dt']} · "
           f"[DART에서 원문 보기](https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rep['rcept_no']})")
sections = rep["sections"]
llm = appdata.llm()


def show(blocks: list, height: int = 520):
    """문단은 문단으로, 표는 표로. 긴 내용은 스크롤 상자 안에."""
    if not blocks:
        st.info("내용이 없습니다.")
        return
    with st.container(height=height):
        st.markdown(docrender.CSS + docrender.to_html(blocks), unsafe_allow_html=True)


def as_text(blocks: list) -> str:
    return docrender.to_text(blocks)


def ai_block(key: str, label: str, instruction: str, document: str):
    if llm is None:
        if not appdata.public_mode():
            st.caption("💡 Anthropic API 키를 넣으면 이 부분을 AI로 요약할 수 있습니다.")
        return
    if st.button(label, key=key, type="primary"):
        with st.spinner("AI가 원문을 읽는 중…"):
            try:
                text, truncated = llm.ask(instruction, document)
            except LLMError as e:
                st.error(str(e))
                return
        if truncated:
            st.warning("원문이 길어 앞부분 15만 자만 분석했습니다.")
        st.markdown(text)
        appdata.ai_disclaimer()


tab1, tab2, tab3, tab4 = st.tabs(["사업의 내용", "경영진단(MD&A)", "감사의견", "재무제표 주석"])

# ---------------------------------------------------------------- 사업의 내용
with tab1:
    sec = disclosure.find_section(sections, "사업의 내용")
    if not sec:
        st.info("'사업의 내용' 섹션을 찾지 못했습니다.")
    else:
        st.markdown("회사가 **무엇을 팔아 어떻게 돈을 버는지**, 시장에서 어떤 위치인지 확인하는 부분입니다. "
                    "특히 '기타 참고사항'에 산업의 특성·경쟁 요소·시장점유율이 정리되어 있는 경우가 많습니다.")
        ai_block("biz", "🤖 수익 구조·경쟁 위치 요약",
                 f"{name}의 사업보고서 '사업의 내용'입니다. 다음을 정리하세요:\n"
                 "1) 수익 창출 구조: 주요 제품·서비스, 제품별 매출 비중, 주요 고객·판매 경로\n"
                 "2) 산업 특성과 경쟁 구도: 시장점유율, 주요 경쟁사, 경쟁 우위 요소\n"
                 "3) 실적에 영향을 주는 요인: 판매가격, 원재료, 환율, 수요, 설비 가동률 등 (수치가 있으면 포함)\n"
                 "4) 연구개발·투자 방향\n5) 분석가가 주목할 점 3가지",
                 as_text(disclosure.full_blocks(sec)))
        subs = sec.children or [sec]
        pick = st.selectbox("세부 항목", [s.title for s in subs])
        chosen = next(s for s in subs if s.title == pick)
        show(disclosure.full_blocks(chosen))

# ---------------------------------------------------------------- MD&A
with tab2:
    sec = disclosure.find_section(sections, "이사의 경영진단")
    if not sec:
        st.info("'이사의 경영진단 및 분석의견' 섹션을 찾지 못했습니다.")
    else:
        st.markdown("경영진이 직접 설명하는 **실적 변동 이유**와 전망입니다.")
        bl = disclosure.full_blocks(sec)
        ai_block("mdna", "🤖 실적 변동 요인 요약",
                 "경영진단 및 분석의견입니다. 1) 매출·영업이익 변동의 주요 원인 2) 부문별 실적 "
                 "3) 유동성·자금조달 상황 4) 경영진이 제시한 전망이나 계획 을 정리하세요.", as_text(bl))
        show(bl)

# ---------------------------------------------------------------- 감사의견
with tab3:
    sec = disclosure.find_section(sections, "회계감사인의 감사의견")
    st.markdown("감사의견은 **적정 / 한정 / 부적정 / 의견거절** 중 하나입니다. 적정이 아니면(변형의견) "
                "재무제표 신뢰성에 문제가 있다는 뜻이므로 근거를 반드시 확인하세요. "
                "핵심감사사항(KAM)은 감사인이 가장 위험하다고 본 계정입니다.")
    if not sec:
        st.info("감사의견 섹션을 찾지 못했습니다. '기업 분석' 페이지 하단의 감사의견 표를 참고하세요.")
    else:
        bl = disclosure.full_blocks(sec)
        ai_block("audit", "🤖 감사의견·핵심감사사항 정리",
                 "회계감사인의 감사의견 섹션입니다. 1) 최근 3개 연도 감사인과 감사의견 "
                 "2) 변형의견이 있다면 그 근거 (없으면 '없음') 3) 강조사항 4) 핵심감사사항과 관련 계정, "
                 "감사인이 우려한 이유 5) 감사인 교체나 비감사용역 등 특이사항 을 정리하세요.", as_text(bl))
        show(bl)

# ---------------------------------------------------------------- 주석
with tab4:
    note_sec = (disclosure.find_section(sections, "연결재무제표 주석")
                or disclosure.find_section(sections, "재무제표 주석"))
    if not note_sec:
        st.info("재무제표 주석 섹션을 찾지 못했습니다.")
    else:
        notes = docrender.split_note_blocks(disclosure.full_blocks(note_sec))
        st.markdown("주석은 재무제표 숫자가 **어떤 회계정책으로, 무엇으로 구성되어** 만들어졌는지 보여줍니다. "
                    "반도체 기업은 재고자산(평가손실), 유형자산(감가상각), 수익인식, 차입금을 먼저 보세요.")
        # 바로가기 이름 -> 주석 제목에서 찾을 키워드 (회사마다 제목이 달라 여러 후보)
        keys = {
            "재고자산": ["재고자산"], "유형자산": ["유형자산"],
            "매출(수익)": ["고객과의 계약", "매출액", "영업수익", "수익"],
            "차입금": ["차입금", "사채"], "충당부채": ["충당부채"],
            "특수관계자": ["특수관계자"], "회계정책": ["중요한 회계정책", "회계처리방침"],
            "회계추정": ["회계추정", "추정 및 가정"],
        }
        quick = st.pills("주요 계정 바로가기", list(keys), key="note_quick")
        titles = [t for t, _ in notes]
        idx = 0
        if quick:
            idx = next((i for kw in keys[quick] for i, t in enumerate(titles) if kw in t), 0)
        pick = st.selectbox(f"주석 선택 (총 {len(notes)}개)", titles, index=idx)
        body = dict(notes)[pick]
        ai_block(f"note_{pick}", "🤖 이 주석 해설",
                 f"{name} 연결재무제표 주석 중 '{pick}'입니다. 1) 이 계정의 회계정책(인식·측정 방법) "
                 "2) 금액 구성과 전기 대비 주요 변동 3) 분석 시 유의할 점(추정·판단이 개입되는 부분, 위험 신호) "
                 "을 초보자도 이해할 수 있게 설명하세요.", as_text(body))
        show(body)
