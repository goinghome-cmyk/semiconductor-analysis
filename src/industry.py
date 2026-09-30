"""산업 분석 로직: 밸류체인 그룹 집계, ECOS 선행지표, 사이클 신호, 이익 집중도.

판정 규칙은 모두 이 파일의 상수·함수에 드러나 있고, 화면에도 그대로 보여준다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .ecos import EcosClient

# 신호 판정 임계값 (화면 '판정 규칙'에 그대로 표시)
TH_YOY_ACCEL = 0.02      # 매출 YoY 가속/감속 판단 폭 (2%p)
TH_OPM = 0.02            # 영업이익률 변화 폭 (2%p)
TH_INV = 0.05            # 재고/매출 비율 상대 변화 (5%)
TH_PRICE = 0.03          # 가격지수 3개월 평균 변화 (3%)
TH_GAP = 0.03            # 출하 YoY - 재고 YoY (3%p)
TH_CAPEX = 0.10          # 설비투자 확대/축소 (10%)
TH_UTIL = 2.0            # 가동률지수 3개월 평균 변화 (포인트)


# ------------------------------------------------------------ 그룹 집계
def company_frame(d: dict) -> pd.DataFrame:
    """산업 집계용 기업 분기 데이터. 부문 실적이 있으면 매출·영업이익을 부문 값으로 교체."""
    q = d["quarterly"][["revenue", "op", "capex", "inventory"]].copy()
    # 재고는 회사 전체 값이므로 비율 계산에는 회사 전체 매출을 쓴다
    q["revenue_total"] = q["revenue"]
    seg = d.get("segment")
    if seg is not None and not seg.empty:
        q["revenue"] = seg["revenue"].reindex(q.index)
        q["op"] = seg["op"].reindex(q.index)
    return q


def group_timeseries(cfg: dict, data: dict[str, dict]) -> pd.DataFrame:
    """(분기, 그룹) 단위 합계와 파생지표. YoY 는 두 시점 모두 있는 기업만."""
    frames = []
    for c in cfg["companies"]:
        d = data.get(c["name"])
        if not d or d["quarterly"].empty:
            continue
        f = company_frame(d)
        f["기업"], f["그룹"] = c["name"], c["group"]
        frames.append(f)
    if not frames:
        return pd.DataFrame()
    allq = pd.concat(frames).rename_axis("분기").reset_index()

    out = []
    for g, sub in allq.groupby("그룹"):
        # pivot_table(sum)은 빈 값을 0으로 만들어 YoY를 왜곡하므로 pivot 사용 (분기·기업은 유일)
        piv = {m: sub.pivot(index="분기", columns="기업", values=m)
               for m in ("revenue", "op", "capex", "inventory", "revenue_total")}
        ts = pd.DataFrame({m: p.sum(axis=1, min_count=1) for m, p in piv.items()})
        for m in ("revenue", "capex"):
            cur, base = piv[m], piv[m].shift(4)
            both = cur.notna() & base.notna()
            ts[f"{m}_yoy"] = cur.where(both).sum(axis=1, min_count=1) / base.where(both).sum(axis=1, min_count=1) - 1
        ts["opm"] = ts["op"] / ts["revenue"]
        ts["inv_ratio"] = ts["inventory"] / ts["revenue_total"]
        ts["capex_ttm"] = ts["capex"].rolling(4, min_periods=4).sum()
        ts["capex_ttm_yoy"] = ts["capex_ttm"] / ts["capex_ttm"].shift(4) - 1
        ts["그룹"] = g
        out.append(ts)
    return pd.concat(out).reset_index().sort_values(["그룹", "분기"])


def group_series(gts: pd.DataFrame, group: str, col: str) -> pd.Series:
    sub = gts[gts["그룹"] == group].set_index("분기")
    return sub[col] if col in sub else pd.Series(dtype=float)


# ------------------------------------------------------------ ECOS 지표
def ecos_indicators(client: EcosClient, cfg: dict, start: str = "201901", end: str = "209912") -> pd.DataFrame:
    """월별 선행지표 표. 열: 가격지수들, 생산/출하/재고, 환율."""
    e = cfg.get("ecos") or {}
    cols = {}
    for p in e.get("price", []):
        cols[p["name"]] = client.series(p["stat"], "M", start, end, *p["items"])
    cyc = e.get("cycle")
    if cyc:
        for key, label in (("production", "생산"), ("shipment", "출하"), ("inventory", "재고")):
            cols[label] = client.series(cyc["stat"], "M", start, end, *cyc[key])
    ut = e.get("utilization")
    if ut:
        cols[f"{ut['label']} 가동률"] = client.series(ut["stat"], "M", start, end, *ut["rate"])
        cols[f"{ut['label']} 생산능력"] = client.series(ut["stat"], "M", start, end, *ut["capacity"])
    fx = e.get("fx")
    if fx:
        cols[fx["name"]] = client.series(fx["stat"], "M", start, end, *fx["items"])
    df = pd.DataFrame(cols)
    df.index = pd.to_datetime(df.index, format="%Y%m")
    return df.sort_index()


# ------------------------------------------------------------ 사이클 신호
@dataclass
class Signal:
    name: str
    status: str          # "개선" / "중립" / "악화" / "정보"
    value: str           # 근거 수치
    rule: str            # 판정 규칙
    meaning: str         # 의미
    watch: str           # 무엇이 바뀌면 전환 신호인가
    scored: bool = True  # 국면 판정 점수에 포함되는지


def _status(delta: float | None, th: float, higher_is_good: bool = True) -> str:
    if delta is None or pd.isna(delta):
        return "중립"
    if not higher_is_good:
        delta = -delta
    return "개선" if delta > th else ("악화" if delta < -th else "중립")


def _pct(v) -> str:
    return "-" if v is None or pd.isna(v) else f"{v * 100:+.1f}%"


def _lvl(v) -> str:
    return "-" if v is None or pd.isna(v) else f"{v * 100:.1f}%"


def cycle_signals(gts: pd.DataFrame, leader: str, eco: pd.DataFrame | None, cfg: dict) -> list[Signal]:
    sigs: list[Signal] = []
    s = gts[gts["그룹"] == leader].set_index("분기").dropna(subset=["revenue"])
    if len(s) >= 2:
        last, prev = s.index[-1], s.index[-2]
        y1, y0 = s.loc[last, "revenue_yoy"], s.loc[prev, "revenue_yoy"]
        sigs.append(Signal(
            f"{leader} 매출 모멘텀", _status(None if pd.isna(y0) else y1 - y0, TH_YOY_ACCEL),
            f"매출 YoY {_pct(y0)} ({prev}) → {_pct(y1)} ({last})",
            f"YoY가 {TH_YOY_ACCEL*100:.0f}%p 넘게 커지면 개선(가속), 작아지면 악화(감속)",
            "성장 속도가 빨라지는지. 사이클은 '수준'보다 '방향'이 먼저 바뀝니다.",
            "YoY가 2분기 연속 줄어들면 확장 후반·둔화의 초기 신호"))
        o1 = s.loc[last, "opm"]
        base_q = f"{int(last[:4]) - 1}{last[4:]}"
        o0 = s["opm"].get(base_q)
        sigs.append(Signal(
            f"{leader} 수익성 방향", _status(None if o0 is None or pd.isna(o0) else o1 - o0, TH_OPM),
            f"영업이익률 {_lvl(o0)} ({base_q}) → {_lvl(o1)} ({last})",
            f"전년 같은 분기보다 {TH_OPM*100:.0f}%p 넘게 오르면 개선, 내리면 악화",
            "판가·가동률이 좋아지면 이익률이 먼저 반응합니다.",
            "이익률이 전분기 대비 꺾이기 시작하면 가격 정점 가능성"))
        i1 = s.loc[last, "inv_ratio"]
        i0 = s["inv_ratio"].get(base_q)
        rel = None if i0 is None or pd.isna(i0) or i0 == 0 else i1 / i0 - 1
        sigs.append(Signal(
            f"{leader} 재고 부담", _status(rel, TH_INV, higher_is_good=False),
            f"재고/분기매출 {i0:.2f} ({base_q}) → {i1:.2f} ({last})" if rel is not None else "-",
            f"전년 대비 {TH_INV*100:.0f}% 넘게 줄면 개선, 늘면 악화",
            "재고가 줄면 공급이 빠듯하다는 뜻. 재고 증가는 수요 둔화의 대표적 선행 신호입니다.",
            "재고/매출 비율이 2분기 연속 오르면 둔화 신호"))
        cx = s["capex_ttm_yoy"].iloc[-1]
        cx_status = "정보"
        sigs.append(Signal(
            f"{leader} 설비투자", cx_status,
            f"CAPEX(최근 4분기 합) 전년 대비 {_pct(cx)}",
            f"국면 점수에는 넣지 않음. ±{TH_CAPEX*100:.0f}%를 확대/축소 기준으로 참고",
            "칩메이커가 업황을 좋게 볼수록 투자를 늘립니다. 장비 업체 수요로 이어집니다.",
            "투자 증가율이 둔화되면 장비 업체 수주에 부정적"))
        sigs[-1].scored = False

    if eco is not None and not eco.empty:
        for p in (cfg.get("ecos") or {}).get("price", [])[:1]:
            col = p["name"]
            ser = eco[col].dropna()
            if len(ser) >= 6:
                m3, p3 = ser.iloc[-3:].mean(), ser.iloc[-6:-3].mean()
                yoy = ser.iloc[-1] / ser.iloc[-13] - 1 if len(ser) >= 13 else np.nan
                sigs.append(Signal(
                    f"메모리 가격 ({col})", _status(m3 / p3 - 1, TH_PRICE),
                    f"최근 3개월 평균 {m3:.1f} vs 직전 3개월 {p3:.1f} ({_pct(m3 / p3 - 1)}), "
                    f"전년 대비 {_pct(yoy)} · 기준월 {ser.index[-1]:%Y-%m}",
                    f"3개월 평균이 직전 3개월보다 {TH_PRICE*100:.0f}% 넘게 오르면 개선, 내리면 악화",
                    "한국은행 수출물가지수(계약통화 기준). 메모리 판가의 대용 지표로, 분기 실적보다 먼저 움직입니다.",
                    "가격 상승률이 둔화되다 하락 전환하면 실적 꺾임의 선행 신호"))
        ut = (cfg.get("ecos") or {}).get("utilization")
        rate_col = f"{ut['label']} 가동률" if ut else None
        if rate_col and rate_col in eco:
            r = eco[rate_col].dropna()
            capa = eco[f"{ut['label']} 생산능력"].dropna()
            if len(r) >= 6:
                m3, p3 = r.iloc[-3:].mean(), r.iloc[-6:-3].mean()
                capa_y = capa.iloc[-1] / capa.iloc[-13] - 1 if len(capa) >= 13 else np.nan
                sigs.append(Signal(
                    f"{ut['label']} 가동률", _status(m3 - p3, TH_UTIL),
                    f"가동률지수(계절조정) 최근 3개월 {m3:.1f} vs 직전 3개월 {p3:.1f} · "
                    f"생산능력 전년 대비 {_pct(capa_y)} · 기준월 {r.index[-1]:%Y-%m}",
                    f"3개월 평균이 직전 3개월보다 {TH_UTIL:.0f}포인트 넘게 오르면 개선, 내리면 악화 (2020년=100)",
                    "통계청 반도체 제조업 가동률지수. 칩메이커 공장이 얼마나 도는지로, 소재·부품 회사에게는 '고객 가동률'입니다. "
                    "생산능력이 가동률보다 빨리 늘면 새 설비가 아직 다 채워지지 않았다는 뜻입니다.",
                    "가동률이 2분기 연속 떨어지면 감산(수요 둔화) 신호"))
        cyc = (cfg.get("ecos") or {}).get("cycle")
        if cyc and {"출하", "재고"} <= set(eco.columns):
            sh = eco["출하"].dropna()
            iv = eco["재고"].dropna()
            if len(sh) >= 15 and len(iv) >= 15:
                sh_y = (sh.rolling(3).mean() / sh.rolling(3).mean().shift(12) - 1).iloc[-1]
                iv_y = (iv.rolling(3).mean() / iv.rolling(3).mean().shift(12) - 1).iloc[-1]
                sigs.append(Signal(
                    "출하-재고 순환", _status(sh_y - iv_y, TH_GAP),
                    f"출하 {_pct(sh_y)} vs 재고 {_pct(iv_y)} (3개월 평균, 전년 대비) · 기준월 {sh.index[-1]:%Y-%m}",
                    f"출하 증가율이 재고 증가율보다 {TH_GAP*100:.0f}%p 넘게 높으면 개선, 낮으면 악화",
                    f"통계청 {cyc['label']} 지수(반도체 포함 업종, 반도체 단독 아님). 팔리는 속도가 쌓이는 속도보다 빠르면 호황 쪽입니다.",
                    "재고 증가율이 출하 증가율을 앞지르면 둔화 신호"))
    return sigs


PHASES = {
    "회복": "이익 수준은 아직 낮지만 지표가 좋아지는 중. 사이클 바닥을 지나는 구간",
    "확장": "이익 수준이 높고 지표도 계속 좋아지는 호황 구간",
    "둔화": "이익 수준은 높지만 지표가 나빠지기 시작한 구간. 정점 통과 가능성",
    "침체": "이익 수준이 낮고 지표도 나빠지는 불황 구간",
}


def cycle_phase(gts: pd.DataFrame, leader: str, sigs: list[Signal]) -> dict:
    """수준(이익률이 기간 중앙값 이상인가) × 방향(개선 신호 - 악화 신호)으로 4국면 판정."""
    s = gts[gts["그룹"] == leader].set_index("분기")["opm"].dropna()
    scored = [x for x in sigs if x.scored]
    score = sum(x.status == "개선" for x in scored) - sum(x.status == "악화" for x in scored)
    if s.empty:
        return {"phase": "판단불가", "score": score, "n": len(scored)}
    level_high = s.iloc[-1] >= s.median()
    if score > 0:
        phase = "확장" if level_high else "회복"
    elif score < 0:
        phase = "둔화" if level_high else "침체"
    else:
        phase = "확장→둔화 전환 구간" if level_high else "침체→회복 전환 구간"
    return {
        "phase": phase, "score": score, "n": len(scored),
        "level_high": level_high, "opm": s.iloc[-1], "opm_median": s.median(),
        "period": f"{s.index[0]}~{s.index[-1]}",
        "up": sum(x.status == "개선" for x in scored),
        "down": sum(x.status == "악화" for x in scored),
    }


# ------------------------------------------------------------ 이익 집중도
def profit_pool(cfg: dict, data: dict[str, dict]) -> pd.DataFrame:
    """기업별 최근 4개 분기 매출·영업이익(부문 기준)과 비중."""
    rows = []
    for c in cfg["companies"]:
        d = data.get(c["name"])
        if not d or d["quarterly"].empty:
            continue
        f = company_frame(d)
        last4 = f.tail(4)
        if last4["revenue"].count() < 4:
            continue
        rows.append({"기업": c["name"], "그룹": c["group"],
                     "매출(TTM)": last4["revenue"].sum(), "영업이익(TTM)": last4["op"].sum()})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["매출 비중"] = df["매출(TTM)"] / df["매출(TTM)"].sum()
    pos = df["영업이익(TTM)"].clip(lower=0)
    df["이익 비중"] = pos / pos.sum()
    return df.sort_values("영업이익(TTM)", ascending=False).reset_index(drop=True)


def hhi(shares: pd.Series) -> float:
    """허핀달-허쉬만 지수 (0~10,000). 2,500 이상이면 고집중으로 보는 것이 일반적."""
    return float((shares * 100).pow(2).sum())


def lead_lag(gts: pd.DataFrame, cfg: dict, lag: int) -> pd.DataFrame:
    ll = cfg.get("lead_lag")
    if not ll:
        return pd.DataFrame()
    lead = group_series(gts, ll["leader"], f"{ll['leader_metric']}_ttm_yoy"
                        if ll["leader_metric"] == "capex" else f"{ll['leader_metric']}_yoy")
    follow = group_series(gts, ll["follower"], f"{ll['follower_metric']}_yoy")
    df = pd.DataFrame({"선행": lead, "후행": follow})
    df[f"선행({lag}분기 전)"] = df["선행"].shift(lag)
    return df
