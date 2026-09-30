"""이익의 질 점검: 손익 ↔ 현금흐름 ↔ 재무상태 ↔ 감사(핵심감사사항) ↔ 주석을 연결한다.

모든 판정 기준은 이 파일의 상수에 있고, 화면에도 그대로 보여준다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

TH_CASH_GOOD = 1.0      # 영업CF / 순이익 이 이 이상이면 양호
TH_CASH_WEAK = 0.5      # 이 미만이면 경고
TH_GAP_WARN = 0.15      # (재고·매출채권 증가율 - 매출 증가율)이 15%p 넘으면 경고
TH_GAP_WATCH = 0.0      # 0 초과면 주의
TH_DAYS_MIN = 5         # 경고는 일수가 5일 이상 늘었을 때만 (원래 일수가 작은 회사의 과민 반응 방지)


@dataclass
class Check:
    key: str
    name: str
    status: str            # "양호" / "주의" / "경고" / "정보" / "판단불가"
    value: str             # 근거 수치
    meaning: str
    rule: str
    notes: list[str] = field(default_factory=list)   # 관련 주석 제목 키워드
    data: dict = field(default_factory=dict)         # 교차 확인용 수치 (주석 충당금 변화 등)


ICON = {"양호": "✅", "주의": "⚠️", "경고": "🔴", "정보": "🔵", "판단불가": "⚪"}


def _pct(v) -> str:
    return "-" if v is None or pd.isna(v) else f"{v * 100:+.1f}%"


def _yoy(s: pd.Series) -> float | None:
    s = s.dropna()
    if len(s) < 5 or s.iloc[-5] in (0, None):
        return None
    return s.iloc[-1] / s.iloc[-5] - 1


def _cum(q: pd.DataFrame, cols: list[str], n: int = 12, min_n: int = 8):
    """최근 n개 분기 중 값이 모두 있는 분기만 더한다. (합계 dict, 분기 수, 시작~끝)"""
    t = q[cols].tail(n).dropna()
    if len(t) < min_n:
        return None, 0, ""
    return t.sum().to_dict(), len(t), f"{t.index[0]}~{t.index[-1]}"


def checks(d: dict, model: dict | None = None, orders: dict | None = None,
           note_nums: dict | None = None) -> list[Check]:
    """model: 설정의 business_models 항목 (inventory: core/normal/light/order)
    orders: 수주산업일 때 수주 정보 {'now', 'prev', 'period', 'contracts'} (appdata.order_info)
    note_nums: 사업보고서 주석 숫자 (notes.extract 결과 + 'report_nm')"""
    q = d["quarterly"]
    last = q.iloc[-1]
    out: list[Check] = []
    inv_mode = (model or {}).get("inventory", "normal")

    # 1. 현금 전환: 이익이 실제 현금으로 들어오는가 (최근 4분기 + 3년 누적)
    ni, cfo = last.get("ni_ttm"), last.get("cfo_ttm")
    assets = q["assets"].dropna()
    avg_assets = (assets.iloc[-1] + assets.iloc[-5]) / 2 if len(assets) >= 5 else assets.iloc[-1]
    s3, n3, span3 = _cum(q, ["cfo", "ni"])
    ratio3 = s3["cfo"] / s3["ni"] if s3 and s3["ni"] > 0 else None
    long_txt = (f" · {n3}개 분기 누적({span3}) {ratio3:.2f}배" if ratio3 is not None else
                f" · {n3}개 분기 누적 순이익 적자" if s3 else "")
    cash_rule = (f"최근 4분기 {TH_CASH_GOOD:.1f}배 이상 양호 · {TH_CASH_WEAK:.1f}~{TH_CASH_GOOD:.1f}배 주의 · "
                 f"{TH_CASH_WEAK:.1f}배 미만 경고. 단, 3년 누적이 {TH_CASH_GOOD:.1f}배 이상이면 최대 '주의'")
    if pd.isna(ni) or pd.isna(cfo):
        out.append(Check("cash", "이익의 현금 전환", "판단불가", "데이터 부족", "", ""))
    elif ni <= 0:
        out.append(Check("cash", "이익의 현금 전환", "정보",
                         f"순이익(최근 4분기) {ni/1e8:,.0f}억 (적자) · 영업CF {cfo/1e8:,.0f}억{long_txt}",
                         "적자라 전환율은 의미가 없습니다. 영업현금흐름이 플러스인지를 보세요.", ""))
    else:
        ratio = cfo / ni
        accrual = (ni - cfo) / avg_assets
        status = "양호" if ratio >= TH_CASH_GOOD else ("주의" if ratio >= TH_CASH_WEAK else "경고")
        meaning = ("장부상 이익이 실제 현금으로 들어오는지. 1배 미만이 계속되면 외상 매출·재고 증가 등으로 "
                   "이익이 현금보다 앞서 있다는 뜻입니다.")
        if status != "양호" and ratio3 is not None and ratio3 >= TH_CASH_GOOD:
            status = "주의"
            meaning += " 최근 1년은 낮지만 3년 누적으로는 이익만큼 현금이 들어와, 일시적(업황 급변기의 운전자본 증가)일 가능성이 있습니다."
        elif status == "양호" and ratio3 is not None and ratio3 < TH_CASH_WEAK:
            meaning += " 최근 1년은 양호하지만 3년 누적으로는 이익보다 현금이 훨씬 적었습니다."
        out.append(Check(
            "cash", "이익의 현금 전환", status,
            f"영업CF ÷ 순이익 = {ratio:.2f}배 (최근 4분기){long_txt} · 발생액 비율 {accrual*100:+.1f}% (총자산 대비)",
            meaning, cash_rule))

    # 1-1. 본업 밖 이익: 세전이익 중 영업이익이 아닌 부분(금융·지분법·처분손익 등)
    out.append(_nonop_check(last))
    # 1-2. 잉여현금흐름
    out.append(_fcf_check(q, last))

    # 2·3. 재고·매출채권이 매출보다 빨리 느는가
    # 분기 말 잔액은 '직전 분기 매출' 규모를 따라가므로, 잔액 ÷ 분기 매출(= 며칠 치 매출인가)을 1년 전과 비교한다.
    for key, col, label, unit, notes, why in (
        ("inventory", "inventory", "재고자산", "보유일수", ["재고자산"],
         "팔리지 않은 재고가 쌓이면 이후 평가손실(이익 감소)이나 가격 인하로 이어질 수 있습니다."),
        ("receivables", "receivables", "매출채권", "회수일수", ["매출채권", "금융상품", "신용위험"],
         "매출은 늘었는데 돈을 못 받고 있다면 매출의 질이 낮거나 공격적으로 인식했을 가능성이 있습니다."),
    ):
        days = (q[col] / q["revenue"] * 91).where(q["revenue"] > 0) if col in q else pd.Series(dtype=float)
        days = days.dropna()
        if len(days) < 5:
            out.append(Check(key, f"{label} {unit}", "판단불가", "데이터 부족", why, "", notes))
            continue
        d1, d0 = days.iloc[-1], days.iloc[-5]
        chg = d1 / d0 - 1 if d0 else None
        status = ("판단불가" if chg is None else
                  "경고" if chg > TH_GAP_WARN and (d1 - d0) >= TH_DAYS_MIN else
                  ("주의" if chg > TH_GAP_WATCH else "양호"))
        value = f"{label} ÷ 분기매출 × 91일 = {d0:.0f}일 ({days.index[-5]}) → {d1:.0f}일 ({days.index[-1]}), {_pct(chg)}"
        title = f"{label} {unit}"
        if key == "inventory":
            status, value, why, title = _inventory_by_model(inv_mode, model, q, status, value, why, title, orders)
        out.append(Check(
            key, title, status, value,
            why + ("" if key == "inventory" and inv_mode in ("light", "order") else
                   f" 매출보다 {label}이 빨리 늘면 이 일수가 길어집니다."),
            f"1년 전보다 줄면 양호 · 0~{TH_GAP_WARN*100:.0f}% 늘면 주의 · "
            f"{TH_GAP_WARN*100:.0f}% 넘게, 그리고 {TH_DAYS_MIN}일 이상 늘면 경고"
            + (" (원가가 아닌 매출 기준이라 근사치)" if key == "inventory" else ""),
            notes))

    # 수주산업: 수주 동향
    if (model or {}).get("backlog"):
        out.append(_order_check(q, orders))

    # 4. 설비투자 강도 (정보): 감가상각비가 있으면 CAPEX/감가상각, 없으면 CAPEX/유형자산
    capex, dep = last.get("capex_ttm"), last.get("dep_ttm")
    ppe = last.get("ppe")
    if pd.notna(capex) and pd.notna(dep) and dep > 0:
        val = f"설비투자 ÷ 감가상각비 = {capex/dep:.2f}배 (최근 4분기)"
        meaning = ("1배를 크게 넘으면 설비를 늘리는 중(성장 투자), 1배 미만이면 유지 수준 이하입니다. "
                   "투자가 클수록 '감가상각을 언제부터 시작하느냐'가 이익률에 큰 영향을 줍니다.")
    elif pd.notna(capex) and pd.notna(ppe) and ppe > 0:
        val = f"설비투자 ÷ 유형자산 = {capex/ppe*100:.1f}% (최근 4분기, 감가상각비 미공시)"
        meaning = ("유형자산 대비 한 해 투자 규모. 높을수록 설비를 빠르게 늘리는 중입니다. "
                   "투자가 클수록 감가상각 개시 시점 판단이 이익률에 큰 영향을 줍니다.")
    else:
        val, meaning = "데이터 부족", ""
    out.append(Check("capex", "설비투자 강도", "정보" if meaning else "판단불가", val, meaning,
                     "판정 없이 참고용", ["유형자산"]))

    # 5. 재작성·재분류
    recent = q.tail(8)
    rs = list(recent.index[recent.get("restated", pd.Series(False, index=recent.index)).fillna(False)])
    out.append(Check(
        "restated", "과거 실적 재작성", "주의" if rs else "양호",
        f"재작성 감지 분기: {', '.join(rs)}" if rs else "최근 8개 분기 중 없음",
        "공시된 3개월 실적과 누적 실적의 차이가 맞지 않는 분기. 사업 매각·분할(중단영업), 회계정책 변경 등으로 "
        "과거 숫자가 바뀐 것입니다. 전년 대비 비교가 왜곡될 수 있어 원인을 확인해야 합니다.",
        "누적 차이와 공시된 3개월 매출이 3% 넘게 다르면 재작성으로 판단",
        ["중단영업", "매각예정", "사업결합", "종속기업"]))

    # 6·7. 주석 숫자: 재고 평가충당금, 매출채권 손실충당금
    if note_nums is not None:
        out.append(_inv_allow_check(note_nums))
        out.append(_rec_allow_check(note_nums))
    return out


# ------------------------------------------------------------------ 본업 밖 이익 · 잉여현금
TH_NONOP = 0.30      # 세전이익의 30% 이상이 본업 밖이면 주의


def _nonop_check(last: pd.Series) -> Check:
    name = "본업 밖 이익 비중"
    meaning = ("세전이익 중 영업이익이 아닌 부분(이자·환율·지분법·자산 처분 손익 등)의 비중. "
               "본업 밖 이익은 매년 반복되기 어려워, 비중이 크면 이익 수준을 그대로 믿기 어렵습니다.")
    rule = f"본업 밖 이익이 세전이익의 {TH_NONOP*100:.0f}% 이상이면 주의 · 본업 밖 손실이 크면 참고"
    pt, op = last.get("pretax_ttm"), last.get("op_ttm")
    if pd.isna(pt) or pd.isna(op):
        return Check("nonop", name, "판단불가", "세전이익 데이터 부족", meaning, rule, ["금융수익", "기타수익", "관계기업"])
    non = pt - op
    base = f"세전이익 {pt/1e8:,.0f}억 = 영업이익 {op/1e8:,.0f}억 + 본업 밖 {non/1e8:+,.0f}억 (최근 4분기)"
    if pt <= 0:
        return Check("nonop", name, "정보", base + " · 세전 적자", meaning, rule, ["금융수익", "기타수익", "관계기업"])
    share = non / pt
    if share >= TH_NONOP:
        status, extra = "주의", f" → 이익의 {share*100:.0f}%가 본업 밖에서 나옴"
    elif share <= -TH_NONOP:
        status, extra = "정보", f" → 본업 밖 손실이 영업이익의 {-non/op*100:.0f}%를 깎음 (이자비용·환손실·손상 등)"
    else:
        status, extra = "양호", f" → 본업 밖 비중 {share*100:+.0f}%"
    return Check("nonop", name, status, base + extra, meaning, rule, ["금융수익", "기타수익", "관계기업", "지분법"])


def _fcf_check(q: pd.DataFrame, last: pd.Series) -> Check:
    name = "잉여현금흐름"
    meaning = ("영업으로 번 현금에서 설비투자를 뺀 돈. 배당·차입 상환·현금 적립에 쓸 수 있는 여력입니다. "
               "마이너스면 투자를 차입이나 보유 현금으로 메우는 중인데, 반도체는 증설기에 일시적으로 마이너스가 흔합니다.")
    cfo, capex = last.get("cfo_ttm"), last.get("capex_ttm")
    s3, n3, span3 = _cum(q, ["cfo", "capex"])
    if pd.isna(cfo) or pd.isna(capex):
        return Check("fcf", name, "판단불가", "데이터 부족", meaning, "판정 없이 참고용")
    f1 = cfo - capex
    txt = f"최근 4분기 {f1/1e8:+,.0f}억 (영업CF {cfo/1e8:,.0f}억 − 설비투자 {capex/1e8:,.0f}억)"
    if s3:
        f3 = s3["cfo"] - s3["capex"]
        txt += f" · {n3}개 분기 누적 {f3/1e8:+,.0f}억"
        if cfo > 0:
            txt += f" · 영업CF 중 설비투자 비중 {capex/cfo*100:.0f}%"
    return Check("fcf", name, "정보", txt, meaning, "판정 없이 참고용")


# ------------------------------------------------------------------ 주석 숫자
TH_INV_ALLOW = 0.03   # 재고 평가충당금 비율이 3%p 넘게 오르면 주의
TH_REC_ALLOW = 0.01   # 매출채권 충당금 비율이 1%p 넘게 오르면 주의
TH_REC_LEVEL = 0.05   # 매출채권의 5% 이상을 못 받을 것으로 보면 주의


def _yr(note_nums: dict) -> str:
    m = re.search(r"\((\d{4})\.\d{2}\)", note_nums.get("report_nm", ""))
    return f"{m.group(1)}년 말" if m else "당기말"


def _inv_allow_check(nn: dict) -> Check:
    name = "재고 평가충당금 (주석)"
    meaning = ("재무상태표 재고는 '안 팔리거나 값이 떨어져 손실로 미리 깎아둔 금액(평가충당금)'을 뺀 순액입니다. "
               "충당금 비율이 오르면 재고의 가치가 떨어지고 있다는 뜻이고, 내리면 과거에 깎아둔 손실이 이익으로 되돌아온 것(환입)일 수 있습니다.")
    rule = (f"총재고 대비 충당금 비율이 1년 새 {TH_INV_ALLOW*100:.0f}%p 넘게 오르면 주의 · "
            f"{TH_INV_ALLOW*100:.0f}%p 넘게 내리면 참고(환입이 이익을 키웠을 수 있음) · 사업보고서 주석 기준")
    inv = nn.get("inventory")
    if not inv:
        return Check("inv_allow", name, "판단불가", "주석에서 재고자산 표를 찾지 못했습니다.", meaning, rule, ["재고자산"])
    cur, prev = inv["cur"], inv.get("prev")
    txt = f"{_yr(nn)} 총재고 {cur['gross']/1e8:,.0f}억 중 충당금 {cur['allow']/1e8:,.0f}억 ({cur['ratio']*100:.1f}%)"
    if not prev:
        return Check("inv_allow", name, "정보", txt, meaning, rule, ["재고자산"])
    delta = cur["ratio"] - prev["ratio"]
    released = prev["allow"] - cur["allow"]
    txt += f" · 1년 전 {prev['ratio']*100:.1f}% → {delta*100:+.1f}%p"
    if delta >= TH_INV_ALLOW:
        status = "주의"
    elif delta <= -TH_INV_ALLOW:
        status = "정보"
        if released > 0:
            txt += f" · 충당금 {released/1e8:,.0f}억 감소(환입·폐기 포함)"
    else:
        status = "양호"
    return Check("inv_allow", name, status, txt, meaning, rule, ["재고자산"],
                 {"delta": delta, "released": released})


def _rec_allow_check(nn: dict) -> Check:
    name = "매출채권 충당금 (주석)"
    meaning = ("받지 못할 것으로 보고 미리 손실 처리한 외상(손실충당금)의 비율. "
               "비율이 높거나 빠르게 오르면 회수가 어려운 거래처가 늘고 있다는 신호입니다. "
               "반대로 외상이 빠르게 느는데 충당금이 그대로면 회수 가능성을 낙관적으로 보고 있을 수 있습니다.")
    rule = (f"충당금 비율 {TH_REC_LEVEL*100:.0f}% 이상 또는 1년 새 {TH_REC_ALLOW*100:.0f}%p 넘게 오르면 주의 · "
            "사업보고서 주석 기준")
    rec = nn.get("receivables")
    if not rec:
        return Check("rec_allow", name, "판단불가", "주석에서 매출채권 표를 찾지 못했습니다.", meaning, rule,
                     ["매출채권", "신용위험"])
    cur, prev = rec["cur"], rec.get("prev")
    txt = f"{_yr(nn)} 매출채권 {cur['gross']/1e8:,.0f}억 중 충당금 {cur['allow']/1e8:,.1f}억 ({cur['ratio']*100:.1f}%)"
    delta = cur["ratio"] - prev["ratio"] if prev else 0
    if prev:
        txt += f" · 1년 전 {prev['ratio']*100:.1f}%"
    status = "주의" if cur["ratio"] >= TH_REC_LEVEL or delta >= TH_REC_ALLOW else "양호"
    return Check("rec_allow", name, status, txt, meaning, rule, ["매출채권", "신용위험"],
                 {"delta": delta if prev else None})


def _level_yoy(s: pd.Series) -> float | None:
    s = s.dropna()
    if len(s) < 5 or not s.iloc[-5]:
        return None
    return s.iloc[-1] / s.iloc[-5] - 1


def _inventory_by_model(mode, model, q, status, value, why, title, orders):
    """사업 형태에 따라 재고 판정을 조정한다."""
    desc = (model or {}).get("desc", "")
    if mode == "core":
        return status, value, why + " (계획생산이라 재고가 핵심 신호입니다.)", title + " (핵심)"
    if mode == "light":
        # 주문생산: 판정하지 않고 참고로만
        return ("정보" if status != "판단불가" else status), value, \
            f"{desc} 그래서 재고 일수는 판정 없이 참고로만 봅니다.", title + " (참고)"
    if mode != "order" or status not in ("주의", "경고"):
        return status, value, why, title
    # 수주산업: 재고가 늘었을 때 선수금·수주잔고도 같이 늘었는지
    inv_g = _level_yoy(q["inventory"])
    cl_g = _level_yoy(q["contract_liab"]) if "contract_liab" in q else None
    bl_g = None
    if orders and orders.get("now", {}).get("status") == "ok" and orders.get("prev", {}).get("status") == "ok":
        p = orders["prev"]["backlog"]
        bl_g = orders["now"]["backlog"] / p - 1 if p else None
    support = []
    if cl_g is not None and inv_g is not None and (cl_g >= inv_g or cl_g >= 0.3):
        support.append(f"선수금(계약부채) {_pct(cl_g)}")
    if bl_g is not None and inv_g is not None and bl_g >= inv_g:
        support.append(f"수주잔고 {_pct(bl_g)}")
    extra = f" · 재고 금액 {_pct(inv_g)}"
    if support:
        return ("정보", value + extra + " · 함께 증가: " + ", ".join(support),
                "재고가 늘었지만 선수금·수주잔고도 함께 늘어, 받은 주문을 만드는 중일 가능성이 큽니다. "
                "수주산업에서는 이런 재고 증가는 부정적 신호로 보지 않습니다.", title + " (수주 대비)")
    note = []
    if cl_g is not None:
        note.append(f"선수금 {_pct(cl_g)}")
    if bl_g is not None:
        note.append(f"수주잔고 {_pct(bl_g)}")
    return (status, value + extra + (" · " + ", ".join(note) if note else ""),
            "수주산업이라 재고 증가가 주문 대응일 수 있지만, 선수금·수주잔고가 재고만큼 늘지 않았습니다. "
            "주문 없이 쌓인 재고인지 확인이 필요합니다.", title + " (수주 대비)")


def _order_check(q: pd.DataFrame, orders: dict | None) -> Check:
    name, notes = "수주 동향", ["매출", "수주", "계약"]
    rule = "수주잔고가 1년 전보다 10% 넘게 늘면 양호 · ±10% 이내 참고 · 10% 넘게 줄면 주의"
    meaning = "수주산업의 선행지표. 수주잔고는 앞으로 매출로 바뀔 일감입니다."
    if not orders:
        return Check("orders", name, "판단불가", "수주 정보를 불러오지 않았습니다.", meaning, rule, notes)
    now, prev = orders.get("now") or {}, orders.get("prev") or {}
    contracts = orders.get("contracts")
    ctext = f" · 최근 1년 공급계약 공시 {contracts}건" if contracts is not None else ""
    if now.get("status") == "ok":
        bl = now["backlog"]
        rev = q["revenue_ttm"].dropna()
        months = bl / (rev.iloc[-1] / 12) if len(rev) and rev.iloc[-1] > 0 else None
        mtxt = f" (최근 4분기 매출의 약 {months:.1f}개월치)" if months else ""
        if prev.get("status") == "ok" and prev["backlog"]:
            g = bl / prev["backlog"] - 1
            status = "양호" if g > 0.10 else ("주의" if g < -0.10 else "정보")
            return Check("orders", name, status,
                         f"수주잔고 {prev['backlog']/1e8:,.0f}억 → {bl/1e8:,.0f}억 ({_pct(g)}, {orders.get('period')}){mtxt}{ctext}",
                         meaning + " 보고서 수주잔고는 별도(본사) 기준인 경우가 많아 연결 매출과 범위가 조금 다를 수 있습니다.",
                         rule, notes)
        return Check("orders", name, "정보", f"수주잔고 {bl/1e8:,.0f}억{mtxt} (1년 전 비교 불가){ctext}", meaning, rule, notes)
    if now.get("status") == "부분":
        pv = f", 1년 전 {prev['orders']/1e8:,.0f}억" if prev.get("status") == "부분" and prev.get("orders") else ""
        return Check("orders", name, "정보",
                     f"수주잔고 비공개 · 보고서에 적은 공시 계약 {now['orders']/1e8:,.0f}억{pv}{ctext}",
                     meaning + " 이 회사는 고객 정보 보호를 이유로 수주잔고를 공개하지 않아, 공시된 공급계약으로 흐름만 봅니다.",
                     rule, notes)
    return Check("orders", name, "정보", f"수주잔고 미공시{ctext}",
                 meaning + " 보고서에 수주잔고가 없어 공급계약 공시 건수로 흐름만 봅니다.", rule, notes)


# ------------------------------------------------------------------ 감사
# 핵심감사사항(KAM) 문구 → 관련 점검 항목(key, 여러 개면 튜플)·주석 키워드
KAM_MAP = [
    (r"재고|순실현", "재고자산", ("inventory", "inv_allow"), ["재고자산"]),
    (r"매출채권|대손|손실충당금", "매출채권 회수", ("receivables", "rec_allow"), ["매출채권", "신용위험"]),
    (r"감가상각|기계장치|유형자산|건설중", "유형자산·감가상각", "capex", ["유형자산"]),
    (r"매출|수익|판매장려|반품|장려금|계약", "매출(수익인식)", "receivables",
     ["고객과의 계약", "영업수익", "매출액", "수익(매출", "수익"]),
    (r"손상|영업권|무형", "자산 손상", None, ["무형자산", "영업권", "손상"]),
    (r"공정가치|금융자산|투자주식|관계기업", "금융자산 공정가치", None, ["공정가치", "금융자산", "관계기업"]),
    (r"충당부채|소송|우발", "충당·우발부채", None, ["충당부채", "우발"]),
    (r"법인세", "법인세", None, ["법인세"]),
    (r"사업결합|인수|지배력", "사업결합", None, ["사업결합", "종속기업"]),
    (r"개발비|연구개발", "개발비 자산화", None, ["무형자산"]),
    (r"특수관계자", "특수관계자 거래", None, ["특수관계자"]),
]


def kam_checks(kam: dict, by_key: dict[str, Check]) -> list[Check]:
    """핵심감사사항 하나와 관련된 점검 항목들."""
    keys = kam.get("metric") or ()
    keys = (keys,) if isinstance(keys, str) else keys
    return [by_key[k] for k in keys if k in by_key]


def audit_summary(aud: pd.DataFrame) -> dict:
    """감사의견 API 결과 → 의견, 감사인 교체 여부, 핵심감사사항 목록(주제 매핑 포함)."""
    if aud is None or aud.empty:
        return {"available": False}
    a = aud.copy()
    a["period"] = a["bsns_year"].astype(str).str.replace(r"\s+", "", regex=True)
    cur = a[a["period"].str.contains("당기")]
    cur = cur if not cur.empty else a.head(2)
    opinions = sorted(set(cur["adt_opinion"].dropna().astype(str)))
    modified = any(k in " ".join(opinions) for k in ("한정", "부적정", "거절"))
    auditors = []
    for label in ("당기", "전기", "전전기"):
        rows = a[a["period"].str.contains(rf"\({label}\)|{label}$", regex=True)]
        if not rows.empty:
            auditors.append((label, str(rows["adtor"].iloc[0])))
    changed = len({x for _, x in auditors}) > 1
    kam_texts, seen = [], set()
    for t in cur.get("core_adt_matter", pd.Series(dtype=str)).dropna().astype(str):
        # '가. …', '1. …', '(1) …', 줄바꿈으로 여러 항목이 붙어 있다
        for part in re.split(r"\n|(?:^|\s)(?:[가-하]\.|\d\.|\(\d\))\s*", t):
            part = re.sub(r"\s+", " ", part).strip(" -")
            part = re.sub(r"^\(?(연결|별도)재무제표\)\s*", "", part).strip()
            part = re.sub(r"^\d+\.\s*", "", part)
            if part.count("(") != part.count(")"):
                part = part.strip("()")
            key = re.sub(r"\s", "", part)
            # 이미 나온 항목의 일부(줄바꿈으로 잘린 조각)면 건너뜀
            if len(key) <= 3 or "해당사항없음" in key or any(key in s for s in seen):
                continue
            seen.add(key)
            kam_texts.append(part)
    kams = []
    for t in kam_texts:
        topic = next(((name, metric, notes) for pat, name, metric, notes in KAM_MAP if re.search(pat, t)),
                     ("기타", None, []))
        kams.append({"text": t, "topic": topic[0], "metric": topic[1], "notes": topic[2]})
    return {"available": True, "opinions": opinions, "modified": modified,
            "auditors": auditors, "auditor_changed": changed, "kams": kams}
