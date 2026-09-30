"""Streamlit 공용: 설정/비밀키 읽기, 캐시된 데이터 로더, 표시 형식."""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from . import disclosure, metrics, news
from . import industry as industry_mod
from .cashflow import classify
from .dart import DartClient, DartError
from .ecos import EcosClient, EcosError
from .llm import DEFAULT_MODEL, LLM

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


# ------------------------------------------------------------------ 설정
def secret(name: str) -> str:
    try:
        v = st.secrets.get(name)
    except Exception:  # secrets.toml 이 없을 때
        v = None
    return v or os.environ.get(name, "")


def public_mode() -> bool:
    """공개(초대) 배포용 모드: 리포트 업로드·개발용 표시·AI 안내를 숨긴다. secrets 의 PUBLIC_MODE = "true"."""
    return str(secret("PUBLIC_MODE")).strip().lower() in ("1", "true", "yes", "on")


DISCLAIMER = (
    "이 앱은 공개 자료를 가공해 보여주는 **학습·포트폴리오용 도구**이며 투자 권유나 종목 추천이 아닙니다. "
    "✅⚠️🔴 판정은 화면에 공개된 규칙에 따른 기계적 결과로 틀릴 수 있고, 투자 판단과 그 책임은 이용자에게 있습니다."
)
SOURCES = ("출처: 금융감독원 전자공시시스템(DART) · 한국은행 경제통계시스템(ECOS, 통계청 작성 통계 포함) · "
           "주가 FinanceDataReader · 뉴스는 각 언론사 기사로 연결되는 링크만 제공")


def footer():
    st.divider()
    fb = secret("FEEDBACK_URL")
    if fb:
        st.markdown(f"💬 [의견 보내기]({fb}) — 틀린 숫자, 헷갈리는 화면, 추가했으면 하는 기능을 알려주세요.")
    st.caption(DISCLAIMER)
    st.caption(SOURCES)


def industries() -> dict[str, dict]:
    out = {}
    for p in sorted(CONFIG_DIR.glob("*.json")):
        cfg = json.loads(p.read_text(encoding="utf-8"))
        out[cfg["industry"]] = cfg
    return out


def _company_picked():
    st.session_state["company"] = st.session_state["company_pick"]


def company_header(title: str, icon: str) -> dict:
    """제목 옆에 기업 선택 상자. 선택한 기업은 페이지를 옮겨도 유지된다 (st.session_state['company']).

    위젯 키('company_pick')와 저장 키('company')를 나눈 이유: 위젯이 없는 페이지(홈·산업)를 거치면
    Streamlit 이 위젯 상태를 지우기 때문에, 선택값은 별도 키에 보관한다."""
    cfg = current_industry()
    names = [c["name"] for c in cfg["companies"]]
    if st.session_state.get("company") not in names:
        st.session_state["company"] = names[0]
    st.session_state["company_pick"] = st.session_state["company"]
    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    c1.title(f"{icon} {title}")
    c2.selectbox("기업 선택", names, key="company_pick", on_change=_company_picked)
    return next(c for c in cfg["companies"] if c["name"] == st.session_state["company"])


def terms(page: str):
    """페이지에 나오는 용어 풀이 (📖 버튼을 누르면 펼쳐짐)."""
    from .glossary import PAGE_TERMS, TERMS
    with st.popover("📖 용어 설명"):
        for k in PAGE_TERMS.get(page, []):
            st.markdown(f"**{k}** — {TERMS[k]}")
        st.caption("전체 용어와 판정 기준은 '지표 설명' 페이지에 있습니다.")


def term_help(key: str) -> str:
    from .glossary import TERMS
    return TERMS.get(key, "")


def current_industry() -> dict:
    inds = industries()
    name = st.session_state.get("industry") or next(iter(inds))
    return inds[name]


PUBLIC_DART_LIMIT = 500  # 공개 모드: 방문자들이 함께 쓰는 키라 더 보수적으로


def dart() -> DartClient:
    from .dart import DAILY_LIMIT
    limit = secret("DART_DAILY_LIMIT")
    limit = int(limit) if str(limit).isdigit() else (PUBLIC_DART_LIMIT if public_mode() else DAILY_LIMIT)
    return DartClient(secret("DART_API_KEY"), daily_limit=limit)


def llm() -> LLM | None:
    key = secret("ANTHROPIC_API_KEY")
    if not key:
        return None
    return LLM(key, secret("ANTHROPIC_MODEL") or DEFAULT_MODEL)


def require_dart():
    if not secret("DART_API_KEY"):
        st.error("DART API 키가 없습니다. `.streamlit/secrets.toml` 에 `DART_API_KEY` 를 넣어주세요. (README 참고)")
        st.stop()


# ------------------------------------------------------------ 데이터 로더
@st.cache_data(ttl=3600, show_spinner=False)
def load_prices(stock_code: str, days: int = 730) -> pd.DataFrame:
    import FinanceDataReader as fdr
    start = (date.today() - timedelta(days=days)).isoformat()
    try:
        return fdr.DataReader(stock_code, start)
    except Exception:
        return pd.DataFrame()


def latest_price(stock_code: str) -> tuple[float | None, str]:
    px = load_prices(stock_code)
    if px.empty:
        return None, ""
    return float(px["Close"].iloc[-1]), px.index[-1].strftime("%Y-%m-%d")


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _load_many(companies: tuple[tuple[str, str, str | None], ...]) -> tuple[dict, list]:
    # DART 과다 호출을 피하려고 한 기업씩 순서대로 (요청 간격은 DartClient 가 관리)
    client = dart()
    results, errors = {}, []
    for name, code, seg_kw in companies:
        try:
            results[name] = metrics.build_company(client, code, segment_keyword=seg_kw)
        except Exception as e:  # 한 기업 실패가 전체를 막지 않도록
            errors.append(f"{name}: {e}")
    return results, errors


def load_all(cfg: dict) -> dict[str, dict]:
    """산업 내 전체 기업을 불러온다. 첫 실행은 2~3분, 이후에는 캐시에서 바로."""
    comps = tuple((c["name"], c["stock_code"], (c.get("business_segment") or {}).get("keyword"))
                  for c in cfg["companies"])
    msg = ("데이터를 준비하는 중입니다… 한동안 방문이 없었다면 20~40초 정도 걸릴 수 있어요." if public_mode() else
           "DART에서 재무제표를 불러오는 중… (처음 한 번은 2~3분 걸립니다. DART 차단을 피하려고 천천히 요청합니다)")
    with st.spinner(msg):
        try:
            results, errors = _load_many(comps)
        except DartError as e:
            st.error(str(e))
            st.stop()
    if errors:
        # 실패한 결과가 6시간 동안 캐시되지 않도록. 성공분은 디스크 캐시에 있어 재호출 비용이 없음
        _load_many.clear()
        with st.expander(f"⚠️ {len(errors)}개 기업을 불러오지 못했습니다"):
            st.write("\n".join(f"- {e}" for e in errors))
    return results


def snapshot(cfg: dict, data: dict[str, dict]) -> pd.DataFrame:
    """기업별 최신 지표 한 줄씩."""
    rows = []
    for c in cfg["companies"]:
        d = data.get(c["name"])
        if not d or d["quarterly"].empty:
            continue
        q = d["quarterly"]
        last = q.iloc[-1]
        price, _ = latest_price(c["stock_code"])
        val = metrics.valuation(last, d["shares"], price)
        cf_type, _, _ = classify(last.get("cfo_ttm"), last.get("cfi_ttm"), last.get("cff_ttm"))
        rows.append({
            "기업": c["name"], "그룹": c["group"], "분야": c["segment"], "기준분기": q.index[-1],
            "매출(TTM,억)": last.get("revenue_ttm") / 1e8 if pd.notna(last.get("revenue_ttm")) else None,
            "영업이익(TTM,억)": last.get("op_ttm") / 1e8 if pd.notna(last.get("op_ttm")) else None,
            "영업이익률(TTM)": last.get("opm_ttm"),
            "영업이익률(분기)": last.get("opm"),
            "매출 YoY": last.get("rev_yoy"),
            "영업이익 YoY": last.get("op_yoy"),
            "ROE": last.get("roe"),
            "부채비율": last.get("debt_ratio"),
            "PER": val["per"], "PBR": val["pbr"],
            "시가총액(억)": val["market_cap"] / 1e8 if val["market_cap"] else None,
            "현금흐름 유형(TTM)": cf_type,
        })
    return pd.DataFrame(rows)


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def _load_ecos(industry: str) -> tuple[pd.DataFrame | None, str]:
    cfg = industries()[industry]
    key = secret("ECOS_API_KEY")
    if not key or not cfg.get("ecos"):
        return None, "ECOS 키가 없어 선행지표를 건너뜁니다."
    try:
        return industry_mod.ecos_indicators(EcosClient(key), cfg), ""
    except EcosError as e:
        return None, str(e)


def load_ecos(cfg: dict) -> pd.DataFrame | None:
    with st.spinner("한국은행 선행지표를 불러오는 중…"):
        df, msg = _load_ecos(cfg["industry"])
    if msg:
        st.caption(f"ℹ️ {msg}")
    return df


def _latest_annual(stock_code: str) -> pd.Series | None:
    """최근 사업보고서 공시 목록의 한 줄 (rcept_no, report_nm, rcept_dt)."""
    client = dart()
    corp = client.corp_code(stock_code)
    end = date.today()
    lst = client.disclosures(corp, (end - timedelta(days=500)).strftime("%Y%m%d"),
                             end.strftime("%Y%m%d"), "A001")
    return None if lst.empty else lst.sort_values("rcept_dt", ascending=False).iloc[0]


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def business_report(stock_code: str) -> dict | None:
    """최근 사업보고서의 섹션 트리와 접수번호."""
    client = dart()
    row = _latest_annual(stock_code)
    if row is None:
        return None
    files = client.document(row["rcept_no"])
    sections = disclosure.parse_sections(disclosure.main_document(files), keep=disclosure.APP_SECTIONS)
    return {"rcept_no": row["rcept_no"], "report_nm": row["report_nm"],
            "rcept_dt": row["rcept_dt"], "sections": sections}


@st.cache_data(ttl=3 * 3600, show_spinner=False)
def news_search(query: str, days: int) -> pd.DataFrame:
    return news.search(query, days)


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def audit(corp_code: str) -> pd.DataFrame:
    """가장 최근 사업연도 감사의견 (작년 → 재작년 순으로 시도)."""
    client = dart()
    for y in (date.today().year - 1, date.today().year - 2):
        a = client.audit_opinions(corp_code, y)
        if not a.empty:
            return a
    return pd.DataFrame()


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def note_blocks(stock_code: str) -> list[tuple[str, list]]:
    """최근 사업보고서의 (연결)재무제표 주석을 번호별로 나눈 블록."""
    from . import docrender
    rep = business_report(stock_code)
    if not rep:
        return []
    sec = (disclosure.find_section(rep["sections"], "연결재무제표 주석")
           or disclosure.find_section(rep["sections"], "재무제표 주석"))
    return docrender.split_note_blocks(disclosure.full_blocks(sec)) if sec else []


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def note_numbers(stock_code: str) -> dict:
    """사업보고서 주석의 재고 평가충당금·매출채권 충당금. 보고서마다 한 번만 파싱해 JSON 으로 남긴다
    (원문 파싱이 수 초 걸려, 9개사를 매번 파싱하면 첫 화면이 느려짐)."""
    from . import notes
    row = _latest_annual(stock_code)
    if row is None:
        return {}
    path = dart().cache_dir / "notes" / f"{row['rcept_no']}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    out = notes.extract(note_blocks(stock_code))
    out["report_nm"] = str(row["report_nm"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def period_report_blocks(stock_code: str, report_period: str) -> dict | None:
    """'YYYY.MM' 기간의 정기보고서(분기·반기·사업) 원문 → 사업의 내용·경영진단·주석 블록."""
    client = dart()
    corp = client.corp_code(stock_code)
    y = int(report_period[:4])
    data = client._get_json("list", {"corp_code": corp, "bgn_de": f"{y}0101", "end_de": f"{y + 1}0630",
                                     "pblntf_ty": "A", "last_reprt_at": "Y", "page_count": "100"},
                            ttl=24 * 3600)
    row = next((r for r in data.get("list", []) if f"({report_period})" in r["report_nm"]), None)
    if not row:
        return None
    files = client.document(row["rcept_no"])
    secs = disclosure.parse_sections(disclosure.main_document(files),
                                     keep=("사업의 내용", "경영진단", "재무에 관한 사항"))
    blocks = []
    for s in secs:
        blocks.append(("p", f"## {s.title}"))
        blocks.extend(disclosure.full_blocks(s))
    return {"report_nm": row["report_nm"], "rcept_no": row["rcept_no"], "blocks": blocks}


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def disclosures_between(stock_code: str, start: str, end: str) -> pd.DataFrame:
    """기간 내 모든 공시 목록 (YYYYMMDD)."""
    client = dart()
    data = client._get_json("list", {"corp_code": client.corp_code(stock_code), "bgn_de": start,
                                     "end_de": end, "page_count": "100"}, ttl=24 * 3600)
    return pd.DataFrame(data.get("list", []))


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def news_between(query: str, start: str, end: str) -> pd.DataFrame:
    return news.search_range(query, start, end)


def _sales_section_blocks(stock_code: str, report_period: str) -> list | None:
    """수주잔고용: '매출 및 수주상황' 블록."""
    return business_subsection(stock_code, report_period, "매출 및 수주")


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def business_subsection(stock_code: str, report_period: str, keyword: str) -> list | None:
    """가벼운 경로: 정기보고서에서 '사업의 내용'만 파싱해 제목에 keyword 가 있는 하위 섹션 블록만 돌려준다."""
    client = dart()
    corp = client.corp_code(stock_code)
    y = int(report_period[:4])
    data = client._get_json("list", {"corp_code": corp, "bgn_de": f"{y}0101", "end_de": f"{y + 1}0630",
                                     "pblntf_ty": "A", "last_reprt_at": "Y", "page_count": "100"},
                            ttl=24 * 3600)
    row = next((r for r in data.get("list", []) if f"({report_period})" in r["report_nm"]), None)
    if not row:
        return None
    secs = disclosure.parse_sections(disclosure.main_document(client.document(row["rcept_no"])),
                                     keep=("사업의 내용",))
    sec = disclosure.find_section(secs, keyword)
    return [("p", f"## {sec.title}")] + disclosure.full_blocks(sec) if sec else []


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def utilization_info(stock_code: str, last_period: str) -> dict:
    """최근 정기보고서의 가동률 공시."""
    from . import utilization
    from .anomaly import period_dates
    _, _, rp = period_dates(last_period)
    bl = business_subsection(stock_code, rp, "생산설비")
    out = utilization.extract(bl) if bl else {"status": "없음", "note": "보고서 없음"}
    out["report_period"] = rp
    return out


def model_of(cfg: dict, name: str) -> dict | None:
    """기업의 사업 형태 설정 (이름 포함)."""
    comp = next((c for c in cfg["companies"] if c["name"] == name), {})
    m = (cfg.get("business_models") or {}).get(comp.get("model"))
    return {**m, "name": comp.get("model")} if m else None


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def order_info(stock_code: str, last_period: str) -> dict:
    """수주산업용: 최근 분기와 1년 전 같은 분기 보고서의 수주잔고 + 최근 1년 공급계약 공시 건수."""
    from . import backlog
    from .anomaly import period_dates
    _, end, rp = period_dates(last_period)
    prev_rp = f"{int(rp[:4]) - 1}{rp[4:]}"
    out = {"period": f"{prev_rp} → {rp}"}
    for key, per in (("now", rp), ("prev", prev_rp)):
        bl = _sales_section_blocks(stock_code, per)
        out[key] = backlog.extract(bl) if bl else {"status": "없음", "note": "보고서 없음"}
    today = date.today()
    disc = disclosures_between(stock_code, (today - timedelta(days=365)).strftime("%Y%m%d"),
                               today.strftime("%Y%m%d"))
    out["contracts"] = int(disc["report_nm"].str.contains("공급계약").sum()) if not disc.empty else 0
    return out


def pick_company_table(df: pd.DataFrame, key: str, **kwargs) -> None:
    """'기업' 열이 있는 표. 행을 누르면 그 기업을 선택(사이드바가 숨는 휴대폰에서도 편하게)."""
    ev = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun",
                      selection_mode="single-row", key=key, **kwargs)
    rows = ev.selection.rows if ev and ev.selection else []
    if rows:
        chosen = df.iloc[rows[0]]["기업"]
        if chosen != st.session_state.get("company"):
            st.session_state["pending_company"] = chosen
            st.rerun()
    st.caption("👆 행을 누르면 그 기업으로 바뀝니다.")


def find_note(notes: list[tuple[str, list]], keywords: list[str]) -> tuple[str, list] | None:
    for kw in keywords:
        for title, bl in notes:
            if kw in title:
                return title, bl
    return None


# ------------------------------------------------------------ 표시 형식
def fmt_pct(v) -> str:
    return "-" if v is None or pd.isna(v) else f"{v * 100:,.1f}%"


def fmt_x(v) -> str:
    return "-" if v is None or pd.isna(v) else f"{v:,.1f}배"


def fmt_won(v, unit: float = 1e8, suffix: str = "억") -> str:
    return "-" if v is None or pd.isna(v) else f"{v / unit:,.0f}{suffix}"


PCT_COLS = ["영업이익률(TTM)", "영업이익률(분기)", "매출 YoY", "영업이익 YoY", "ROE", "부채비율"]


def style_snapshot(df: pd.DataFrame):
    fmt = {c: (lambda v: fmt_pct(v)) for c in PCT_COLS if c in df}
    fmt.update({c: (lambda v: fmt_x(v)) for c in ("PER", "PBR") if c in df})
    fmt.update({c: "{:,.0f}" for c in ("매출(TTM,억)", "영업이익(TTM,억)", "시가총액(억)") if c in df})
    return df.style.format(fmt, na_rep="-")


def ai_disclaimer():
    st.caption("AI 요약은 원문을 바탕으로 생성되며 틀릴 수 있습니다. 중요한 수치는 원문으로 확인하세요.")
