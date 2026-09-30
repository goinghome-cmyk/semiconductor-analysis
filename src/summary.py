"""홈 화면 '지금 한눈에': 데이터에서 뽑은 사실 문장. 판단이 섞인 문장은 '규칙 기반'임을 밝힌다."""
from __future__ import annotations

import pandas as pd

from . import anomaly, industry, quality


def _pct(v) -> str:
    return "-" if v is None or pd.isna(v) else f"{v * 100:+.0f}%"


def data_asof(data: dict, eco: pd.DataFrame | None, price_date: str) -> str:
    quarters = [d["quarterly"].index[-1] for d in data.values() if not d["quarterly"].empty]
    parts = [f"재무 {max(quarters)}" if quarters else None,
             f"주가 {price_date} 종가" if price_date else None]
    if eco is not None and not eco.empty:
        parts.append(f"한국은행 지표 {eco.dropna(how='all').index[-1]:%Y-%m}")
    return " · ".join(p for p in parts if p)


def facts(cfg: dict, data: dict, gts: pd.DataFrame, eco: pd.DataFrame | None,
          sigs: list, phase: dict, pool: pd.DataFrame, quality_by_company: dict) -> list[str]:
    out = []
    chain = [g["group"] for g in cfg.get("value_chain", [])]
    leader = chain[0] if chain else None

    # 1. 업황 국면 (규칙 기반)
    bad = [s.name for s in sigs if s.scored and s.status == "악화"]
    good = [s.name for s in sigs if s.scored and s.status == "개선"]
    line = f"**업황 국면: {phase.get('phase', '판단불가')}** (규칙 기반 판정) — 개선 신호 {len(good)}개, 악화 신호 {len(bad)}개"
    if bad:
        line += f". 나빠지는 신호: {', '.join(bad)}"
    out.append(line)

    # 2. 메모리 가격
    if eco is not None:
        for p in (cfg.get("ecos") or {}).get("price", [])[:1]:
            s = eco.get(p["name"])
            if s is not None and len(s.dropna()) >= 13:
                s = s.dropna()
                out.append(f"**{p['name']}**는 전년 대비 **{_pct(s.iloc[-1] / s.iloc[-13] - 1)}** ({s.index[-1]:%Y-%m})")

    # 3. 밸류체인별 매출 성장
    if not gts.empty and chain:
        last = gts.dropna(subset=["revenue_yoy"]).groupby("그룹").last()
        per = gts.dropna(subset=["revenue_yoy"])["분기"].max()
        parts = [f"{g} {_pct(last.loc[g, 'revenue_yoy'])}" for g in chain if g in last.index]
        line = f"**매출 성장률(전년 동기 대비, {per})**: " + " · ".join(parts)
        if leader in last.index:
            others = [last.loc[g, "revenue_yoy"] for g in chain[1:] if g in last.index]
            if others and last.loc[leader, "revenue_yoy"] > 2 * max(others) > 0:
                line += f" → 호황이 **{leader}**에 몰려 있음"
        out.append(line)

    # 4. 이익 집중
    if not pool.empty:
        top = pool.head(2)
        # 굵은 글씨 뒤에 조사가 바로 붙으면 마크다운이 적용되지 않아, 굵게는 제목 부분에만 쓴다
        out.append(f"**이익 집중**: 최근 4분기 영업이익의 {top['이익 비중'].sum()*100:.1f}%가 "
                   f"{'·'.join(top['기업'])} 두 곳에서 나왔습니다 (이 앱이 다루는 {len(pool)}개사 기준)")

    # 5. 설비투자·생산능력·가동률
    bits = []
    if leader:
        cx = industry.group_series(gts, leader, "capex_ttm_yoy").dropna()
        if len(cx):
            bits.append(f"{leader} 설비투자 {_pct(cx.iloc[-1])} (최근 4분기)")
    ut = (cfg.get("ecos") or {}).get("utilization")
    if eco is not None and ut and f"{ut['label']} 생산능력" in eco:
        cap = eco[f"{ut['label']} 생산능력"].dropna()
        rate = eco[f"{ut['label']} 가동률"].dropna()
        if len(cap) >= 13:
            bits.append(f"반도체 생산능력지수 {_pct(cap.iloc[-1] / cap.iloc[-13] - 1)}")
        if len(rate):
            bits.append(f"가동률지수 {rate.iloc[-1]:.0f} (2020=100)")
    if bits:
        out.append("**설비·가동**: " + " · ".join(bits))

    # 6. 기업 신호 (이익의 질 경고, 최근 재작성)
    warn = [f"{n}({', '.join(c.name.split(' (')[0] for c in cs if c.status == '경고')})"
            for n, cs in quality_by_company.items() if any(c.status == "경고" for c in cs)]
    if warn:
        out.append("**이익의 질 경고**가 있는 기업: " + " · ".join(warn))
    restated = []
    for n, d in data.items():
        for a in anomaly.detect(d["quarterly"]):
            if a.kind == "restated":
                restated.append(f"{n} {a.period}")
    if restated:
        out.append("**과거 실적 재작성** 감지: " + " · ".join(restated) + " (사업 매각·분할 등 확인 필요)")
    return out
