"""업황 민감도: 업황 동인(메모리 가격, 반도체 가동률)이 변할 때 기업 매출 성장률이 얼마나 따라 움직였나.

단순 회귀:  매출 YoY = α + β × 동인 YoY(시차 L분기)
- β(민감도): 동인이 1%p 더 오를 때 매출 성장률이 몇 %p 더 오르는 경향인가
- r(상관계수): 그 관계가 얼마나 일관적인가 (1에 가까울수록)
기업 데이터가 약 3년이라 표본이 10개 안팎입니다. 방향과 크기를 가늠하는 참고용입니다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import industry

MIN_N = 6
LAGS = (0, 1)
R_HIGH, R_MID = 0.7, 0.4     # 상관계수: 뚜렷 / 약하게
B_BIG, B_MID = 0.8, 0.3      # 기울기: 크게 / 보통 / 작게


@dataclass
class Result:
    company: str
    driver: str
    beta: float | None
    alpha: float | None
    r: float | None
    n: int
    lag: int
    level: str
    points: pd.DataFrame  # 분기, 동인 YoY, 매출 YoY


def quarterly_yoy(monthly: pd.Series) -> pd.Series:
    """월별 지수 → 분기 평균 → 전년 대비 변화율. index: 'YYYYQn'."""
    s = monthly.dropna()
    if s.empty:
        return pd.Series(dtype=float)
    qa = s.groupby([s.index.year, s.index.quarter]).mean()
    qa.index = [f"{y}Q{q}" for y, q in qa.index]
    return qa / qa.shift(4) - 1


def drivers(eco: pd.DataFrame | None, cfg: dict) -> dict[str, pd.Series]:
    out = {}
    if eco is None or eco.empty:
        return out
    e = cfg.get("ecos") or {}
    for p in e.get("price", [])[:1]:
        if p["name"] in eco:
            out[f"메모리 가격 ({p['name']})"] = quarterly_yoy(eco[p["name"]])
    ut = e.get("utilization")
    if ut and f"{ut['label']} 가동률" in eco:
        out[f"{ut['label']} 가동률"] = quarterly_yoy(eco[f"{ut['label']} 가동률"])
    return out


def _fit(x: pd.Series, y: pd.Series):
    df = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(df) < MIN_N or df["x"].std() == 0:
        return None
    beta, alpha = np.polyfit(df["x"], df["y"], 1)
    r = float(np.corrcoef(df["x"], df["y"])[0, 1])
    return beta, alpha, r, df


def company(name: str, d: dict, driver_name: str, driver_yoy: pd.Series) -> Result:
    f = industry.company_frame(d)  # 부문 실적이 있으면 부문 기준 (삼성전자 DS)
    rev = f["revenue"]
    base = rev.shift(4)
    y = (rev / base - 1).where(base > 0)
    best = None
    for lag in LAGS:
        x = driver_yoy.shift(lag).reindex(y.index)
        fit = _fit(x, y)
        if fit and (best is None or abs(fit[2]) > abs(best[1][2])):
            best = (lag, fit)
    if best is None:
        return Result(name, driver_name, None, None, None, int(y.notna().sum()), 0, "판단불가", pd.DataFrame())
    lag, (beta, alpha, r, df) = best
    # 두 축을 따로: 관계가 뚜렷한가(r) × 얼마나 크게 움직이나(β)
    if r <= -R_MID:
        level = "반대 방향"
    elif r < R_MID:
        level = "관계 불명확"
    else:
        size = "크게" if beta >= B_BIG else ("보통으로" if beta >= B_MID else "작게")
        level = f"{'뚜렷' if r >= R_HIGH else '약하게'} · {size} 연동"
    pts = df.rename(columns={"x": "동인 YoY", "y": "매출 YoY"}).rename_axis("분기").reset_index()
    return Result(name, driver_name, float(beta), float(alpha), r, len(df), lag, level, pts)


def table(cfg: dict, data: dict, eco: pd.DataFrame | None) -> tuple[pd.DataFrame, dict]:
    """기업 × 동인 결과 표와 {(기업, 동인): Result}."""
    drv = drivers(eco, cfg)
    rows, res = [], {}
    for c in cfg["companies"]:
        d = data.get(c["name"])
        if not d or d["quarterly"].empty:
            continue
        for dn, dy in drv.items():
            r = company(c["name"], d, dn, dy)
            res[(c["name"], dn)] = r
            rows.append({"기업": c["name"], "그룹": c["group"], "사업 형태": c.get("model", "-"),
                         "동인": dn, "민감도": r.level,
                         "β (동인 1%p당 매출 %p)": r.beta, "상관계수 r": r.r,
                         "시차(분기)": r.lag, "표본": r.n})
    return pd.DataFrame(rows), res
