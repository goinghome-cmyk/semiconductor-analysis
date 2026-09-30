"""DART 재무제표 원자료 -> 분기/연간 시계열 및 지표 계산."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .dart import (DartClient, REPRT_FY, REPRT_H1, REPRT_Q1, REPRT_Q3,
                   REPRT_ORDER, REPRT_QUARTER, to_number)

# 계정 매핑: (재무제표 구분, account_id 후보, 계정명 후보)
# account_id 는 접두어(ifrs-full_, ifrs_, dart_)를 뗀 형태로 비교합니다.
FLOW_ACCOUNTS = {
    "revenue": (["IS", "CIS"], ["Revenue"], ["매출액", "수익(매출액)", "영업수익", "매출"]),
    "op": (["IS", "CIS"], ["OperatingIncomeLoss"], ["영업이익", "영업이익(손실)", "영업손익"]),
    "ni": (["IS", "CIS"], ["ProfitLoss"], ["당기순이익", "당기순이익(손실)", "분기순이익", "반기순이익"]),
    # 영업이익과의 차이 = 본업 밖 손익(금융·지분법·자산처분 등). 이익의 질 점검용
    "pretax": (["IS", "CIS"], ["ProfitLossBeforeTax"],
               ["법인세비용차감전순이익", "법인세비용차감전순이익(손실)", "법인세차감전순이익",
                "법인세비용차감전계속영업이익", "법인세비용차감전계속영업이익(손실)"]),
    "ni_owner": (["IS", "CIS"], ["ProfitLossAttributableToOwnersOfParent"],
                 ["지배기업의 소유주에게 귀속되는 당기순이익", "지배기업소유주지분", "지배기업 소유주지분"]),
    "cfo": (["CF"], ["CashFlowsFromUsedInOperatingActivities"], ["영업활동현금흐름", "영업활동으로 인한 현금흐름"]),
    "cfi": (["CF"], ["CashFlowsFromUsedInInvestingActivities"], ["투자활동현금흐름", "투자활동으로 인한 현금흐름"]),
    "cff": (["CF"], ["CashFlowsFromUsedInFinancingActivities"], ["재무활동현금흐름", "재무활동으로 인한 현금흐름"]),
    "capex": (["CF"], ["PurchaseOfPropertyPlantAndEquipment",
                       "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
              ["유형자산의 취득", "유형자산의취득", "투자활동으로 분류된 유형자산의 취득"]),
    "dep": (["CF"], ["AdjustmentsForDepreciationExpense"],
            ["감가상각비", "감가상각비에 대한 조정", "감가상각비 조정", "유형자산감가상각비"]),
    "inv_writedown": (["CF"], ["AdjustmentsForWritedownsOfInventories"],
                      ["재고자산평가손실", "재고자산평가손실 조정", "재고자산평가손실(환입)"]),
}
# 손익계산서 항목: 분/반기 보고서는 3개월 금액(thstrm)과 누적(thstrm_add)을 따로 공시한다
IS_KEYS = ("revenue", "op", "ni", "ni_owner", "pretax")
STOCK_ACCOUNTS = {
    "assets": (["BS"], ["Assets"], ["자산총계"]),
    "liabilities": (["BS"], ["Liabilities"], ["부채총계"]),
    "equity": (["BS"], ["Equity"], ["자본총계"]),
    "equity_owner": (["BS"], ["EquityAttributableToOwnersOfParent"], ["지배기업의 소유주에게 귀속되는 자본", "지배기업 소유주지분"]),
    "cash": (["BS"], ["CashAndCashEquivalents"], ["현금및현금성자산"]),
    "inventory": (["BS"], ["Inventories"], ["재고자산"]),
    "receivables": (["BS"], ["CurrentTradeReceivables", "ShortTermTradeReceivable", "TradeAndOtherCurrentReceivables"],
                    ["매출채권", "유동매출채권", "매출채권및기타유동채권", "매출채권 및 기타유동채권",
                     "매출채권및기타채권", "매출채권 및 기타채권"]),
    "ppe": (["BS"], ["PropertyPlantAndEquipment"], ["유형자산"]),
    # 고객에게 미리 받은 돈(선수금). 수주산업에서 재고 증가를 해석할 때 쓴다
    "contract_liab": (["BS"], ["CurrentContractLiabilities", "ContractLiabilities"],
                      ["유동계약부채", "계약부채", "선수금"]),
}

LABELS = {
    "revenue": "매출액", "op": "영업이익", "ni": "당기순이익", "ni_owner": "지배주주순이익",
    "cfo": "영업CF", "cfi": "투자CF", "cff": "재무CF", "capex": "CAPEX",
    "assets": "자산총계", "liabilities": "부채총계", "equity": "자본총계",
    "equity_owner": "지배주주자본", "cash": "현금", "inventory": "재고자산",
    "opm": "영업이익률", "rev_yoy": "매출 YoY", "rev_qoq": "매출 QoQ",
    "op_yoy": "영업이익 YoY", "op_qoq": "영업이익 QoQ", "roe": "ROE(TTM)",
    "debt_ratio": "부채비율", "npm": "순이익률",
}


def _norm_id(account_id: str) -> str:
    for prefix in ("ifrs-full_", "ifrs_", "dart_"):
        if account_id.startswith(prefix):
            return account_id[len(prefix):]
    return account_id


def _index_rows(df: pd.DataFrame) -> dict:
    """보고서 한 건을 (재무제표, 정규화 id / 공백 뺀 계정명) -> 첫 행 으로 색인.
    DataFrame 을 계정마다 거르면 느려서(보고서당 수백 ms) 한 번만 훑는다."""
    idx = {}
    cols = ["sj_div", "account_id", "account_nm", "thstrm_amount", "thstrm_add_amount"]
    for r in df.reindex(columns=cols).to_dict("records"):
        sj = r["sj_div"]
        for key in (("id", _norm_id(str(r["account_id"] or ""))),
                    ("nm", str(r["account_nm"] or "").replace(" ", ""))):
            idx.setdefault((sj, *key), r)
    return idx


def _find_row(idx: dict, sj_divs, ids, names) -> dict | None:
    for sj in sj_divs:
        for acc_id in ids:
            if (sj, "id", acc_id) in idx:
                return idx[(sj, "id", acc_id)]
    for sj in sj_divs:
        for nm in names:
            if (sj, "nm", nm.replace(" ", "")) in idx:
                return idx[(sj, "nm", nm.replace(" ", ""))]
    return None


def _extract(df: pd.DataFrame) -> dict:
    """보고서 하나에서 필요한 값 추출. 손익/현금흐름은 '누적' 금액 기준."""
    df = _index_rows(df)
    out = {}
    for key, (sj, ids, names) in FLOW_ACCOUNTS.items():
        row = _find_row(df, sj, ids, names)
        if row is None:
            out[key] = None
            continue
        cum = to_number(row.get("thstrm_add_amount"))
        out[key] = cum if cum is not None else to_number(row.get("thstrm_amount"))
        if key in IS_KEYS:
            # 누적이 따로 있으면 thstrm 은 3개월 금액
            out[f"{key}_3m"] = to_number(row.get("thstrm_amount")) if cum is not None else None
    for key, (sj, ids, names) in STOCK_ACCOUNTS.items():
        row = _find_row(df, sj, ids, names)
        out[key] = None if row is None else to_number(row.get("thstrm_amount"))
    for k in ("capex", "dep", "inv_writedown"):
        if out.get(k) is not None:
            out[k] = abs(out[k])
    return out


def _report_due(year: int, reprt_code: str, today: date) -> bool:
    """공시 기한이 지났을 가능성이 있는 보고서만 요청 (불필요한 호출 절약)."""
    due = {
        REPRT_Q1: date(year, 5, 1),
        REPRT_H1: date(year, 8, 1),
        REPRT_Q3: date(year, 11, 1),
        REPRT_FY: date(year + 1, 3, 1),
    }[reprt_code]
    return today >= due


def build_company(client: DartClient, stock_code: str, years: int = 3,
                  today: date | None = None, segment_keyword: str | None = None) -> dict:
    """한 기업의 분기/연간 시계열과 주식수 등을 만든다.
    segment_keyword 가 있으면(예: 삼성전자 'DS') 해당 부문의 분기 매출·영업이익도 함께."""
    today = today or date.today()
    corp_code = client.corp_code(stock_code)
    start = today.year - years + 1

    raw = []  # (year, quarter, values, fs_div, reprt_code)
    fs_pref = None  # 한 번 연결/별도가 확인되면 그쪽만 요청 (호출 절약)
    for y in range(start - 1, today.year + 1):  # YoY 계산을 위해 1년 더
        for rc in REPRT_ORDER:
            if not _report_due(y, rc, today):
                continue
            df, fs_div = client.financials(corp_code, y, rc, fs_pref)
            if fs_div:
                fs_pref = fs_div
            if df.empty:
                continue
            raw.append((y, REPRT_QUARTER[rc], _extract(df), fs_div, rc))

    if not raw:
        return {"corp_code": corp_code, "quarterly": pd.DataFrame(),
                "annual": pd.DataFrame(), "shares": None, "fs_div": ""}

    cum = pd.DataFrame(
        [{"year": y, "q": q, **vals} for y, q, vals, _, _ in raw]
    ).sort_values(["year", "q"]).reset_index(drop=True)

    flow_keys = list(FLOW_ACCOUNTS)
    stock_keys = list(STOCK_ACCOUNTS)
    three_keys = [f"{k}_3m" for k in IS_KEYS]
    for k in three_keys:
        if k not in cum:
            cum[k] = np.nan
    num_cols = flow_keys + stock_keys + three_keys
    cum[num_cols] = cum[num_cols].apply(pd.to_numeric, errors="coerce")

    # 누적 -> 분기 금액 (같은 연도의 직전 누적치를 빼기)
    qdf = cum.copy()
    has_prev_q = cum.groupby("year")["q"].shift(1) == cum["q"] - 1
    for key in flow_keys:
        prev = cum.groupby("year")[key].shift(1)
        diff = np.where(cum["q"] == 1, cum[key], np.where(has_prev_q, cum[key] - prev, np.nan))
        qdf[key] = diff
        if key in IS_KEYS:
            # 1~3분기는 공시된 3개월 금액을 우선 사용. 회사가 과거 기간을 재작성(중단영업 재분류 등)하면
            # 누적 차이가 실제 분기 실적과 달라지기 때문. 4분기는 '연간 - 3분기 누적'.
            three = cum[f"{key}_3m"]
            qdf[key] = np.where((cum["q"] < 4) & three.notna(), three, diff)
    # 재작성 신호: 누적 차이와 공시된 3개월 매출이 3% 넘게 다르면
    rev_diff = pd.Series(np.where(has_prev_q, cum["revenue"] - cum.groupby("year")["revenue"].shift(1), np.nan))
    rev3 = cum["revenue_3m"]
    qdf["restated"] = ((cum["q"].between(2, 3)) & rev3.notna() & rev_diff.notna()
                       & ((rev_diff - rev3).abs() > 0.03 * rev3.abs()))
    qdf["period"] = qdf["year"].astype(str) + "Q" + qdf["q"].astype(str)
    qdf = qdf.set_index("period")
    qdf = _add_quarter_metrics(qdf)

    # 연간 (사업보고서 기준)
    adf = cum[cum["q"] == 4].set_index("year")[flow_keys + stock_keys].copy()
    adf = _add_annual_metrics(adf)

    # 분기는 받아온 전체(추세·TTM 계산용), 연간은 요청 기간 + 1년
    adf = adf[adf.index >= start - 1]

    latest_y, _, _, fs_div, latest_rc = raw[-1]
    shares = client.shares(corp_code, latest_y, latest_rc)

    segment = None
    if segment_keyword:
        from .segments import segment_cumulative, segment_quarterly
        cum_seg = segment_cumulative(client, corp_code, [(y, rc) for y, _, _, _, rc in raw],
                                     segment_keyword)
        segment = segment_quarterly(cum_seg)
        if not segment.empty:
            segment["opm"] = _safe_div(segment["op"], segment["revenue"])
            segment = segment[segment.index.isin(qdf.index)]
    return {"corp_code": corp_code, "quarterly": qdf, "annual": adf,
            "shares": shares, "fs_div": fs_div, "segment": segment}


def _safe_div(a, b):
    a = pd.to_numeric(a, errors="coerce")
    b = pd.to_numeric(b, errors="coerce")
    with np.errstate(divide="ignore", invalid="ignore"):
        r = a / b
    return r.where(b.abs() > 0)


def _growth(cur, base):
    """성장률. 기준값이 음수(적자)면 의미가 없어 NaN."""
    base = pd.to_numeric(base, errors="coerce")
    return _safe_div(pd.to_numeric(cur, errors="coerce") - base, base).where(base > 0)


def _add_quarter_metrics(q: pd.DataFrame) -> pd.DataFrame:
    q = q.copy()
    # ni_owner 가 없으면(별도재무제표 등) 당기순이익으로 대체
    q["ni_owner"] = q["ni_owner"].fillna(q["ni"])
    q["equity_owner"] = q["equity_owner"].fillna(q["equity"])
    q["opm"] = _safe_div(q["op"], q["revenue"])
    q["npm"] = _safe_div(q["ni_owner"], q["revenue"])
    q["rev_qoq"] = _growth(q["revenue"], q["revenue"].shift(1))
    q["op_qoq"] = _growth(q["op"], q["op"].shift(1))
    # YoY: 같은 분기 전년
    key = q["year"].astype(int) * 10 + q["q"].astype(int)
    prev = q.set_index(key)[["revenue", "op"]]
    prev_key = key - 10
    for col, out in (("revenue", "rev_yoy"), ("op", "op_yoy")):
        base = pd.Series(prev[col].reindex(prev_key).values, index=q.index)
        q[out] = _growth(q[col], base)
    # TTM (최근 4개 분기 합)
    for k in ("revenue", "op", "ni", "ni_owner", "pretax", "cfo", "cfi", "cff", "capex", "dep", "inv_writedown"):
        q[f"{k}_ttm"] = q[k].rolling(4, min_periods=4).sum()
    avg_eq = (q["equity_owner"] + q["equity_owner"].shift(4)) / 2
    q["roe"] = _safe_div(q["ni_owner_ttm"], avg_eq.fillna(q["equity_owner"]))
    q["opm_ttm"] = _safe_div(q["op_ttm"], q["revenue_ttm"])
    q["debt_ratio"] = _safe_div(q["liabilities"], q["equity"])
    return q


def _add_annual_metrics(a: pd.DataFrame) -> pd.DataFrame:
    a = a.copy()
    a["ni_owner"] = a["ni_owner"].fillna(a["ni"])
    a["equity_owner"] = a["equity_owner"].fillna(a["equity"])
    a["opm"] = _safe_div(a["op"], a["revenue"])
    a["npm"] = _safe_div(a["ni_owner"], a["revenue"])
    a["rev_yoy"] = _growth(a["revenue"], a["revenue"].shift(1))
    a["op_yoy"] = _growth(a["op"], a["op"].shift(1))
    avg_eq = (a["equity_owner"] + a["equity_owner"].shift(1)) / 2
    a["roe"] = _safe_div(a["ni_owner"], avg_eq.fillna(a["equity_owner"]))
    a["debt_ratio"] = _safe_div(a["liabilities"], a["equity"])
    return a


def valuation(latest: pd.Series, shares: dict | None, price: float | None,
              forward_eps: float | None = None) -> dict:
    """최근 분기 행 + 유통주식수 + 주가 -> EPS/BPS/PER/PBR/시가총액."""
    out = {"eps_ttm": None, "bps": None, "per": None, "pbr": None,
           "market_cap": None, "forward_per": None}
    if not shares or not shares.get("outstanding"):
        return out
    n = shares["outstanding"]
    ni = latest.get("ni_owner_ttm")
    eq = latest.get("equity_owner")
    if pd.notna(ni):
        out["eps_ttm"] = ni / n
    if pd.notna(eq):
        out["bps"] = eq / n
    if price:
        out["market_cap"] = price * n
        if out["eps_ttm"] and out["eps_ttm"] > 0:
            out["per"] = price / out["eps_ttm"]
        if out["bps"] and out["bps"] > 0:
            out["pbr"] = price / out["bps"]
        if forward_eps and forward_eps > 0:
            out["forward_per"] = price / forward_eps
    return out
