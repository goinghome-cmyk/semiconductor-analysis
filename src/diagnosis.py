"""기업 종합 진단: 산업·재무·현금흐름·이익의 질·감사·이상치·밸류에이션을 한 장의 체크리스트로.

각 항목은 (판정, 근거, 자세히 볼 페이지)를 가진다. 모두 앞선 모듈의 결과를 조합할 뿐 새로운 판단 규칙은 최소화한다.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import anomaly, industry, quality
from .cashflow import classify

GOOD, WATCH, BAD, INFO, NA = "양호", "주의", "경고", "정보", "판단불가"
ICON = {GOOD: "✅", WATCH: "⚠️", BAD: "🔴", INFO: "🔵", NA: "⚪"}

CF_STATUS = {"우량형": GOOD, "성장형": GOOD, "현금축적형": INFO, "구조조정형": INFO,
             "재활형": WATCH, "쇠퇴형": WATCH, "위험형": BAD, "도산형": BAD}


@dataclass
class Item:
    area: str
    status: str
    headline: str
    evidence: str
    page: str          # views/*.py
    page_label: str


def _pct(v) -> str:
    return "-" if v is None or pd.isna(v) else f"{v * 100:+.1f}%"


def build(name: str, cfg: dict, data: dict, snap: pd.DataFrame, gts: pd.DataFrame,
          phase: dict, audit_df: pd.DataFrame | None,
          model: dict | None = None, orders: dict | None = None, sens=None,
          note_nums: dict | None = None) -> list[Item]:
    """sens: sensitivity.Result (메모리 가격 동인), note_nums: 사업보고서 주석 숫자"""
    comp = next(c for c in cfg["companies"] if c["name"] == name)
    group = comp["group"]
    d = data[name]
    q = d["quarterly"]
    last = q.iloc[-1]
    items: list[Item] = []

    # 1. 산업 국면
    ph = phase.get("phase", "판단불가")
    st_ = GOOD if ph in ("확장", "회복") else (BAD if ph == "침체" else WATCH if ph != "판단불가" else NA)
    items.append(Item("산업", st_, f"업황 국면: {ph}",
                      f"칩메이커 기준 개선 신호 {phase.get('up', 0)}개 · 악화 신호 {phase.get('down', 0)}개",
                      "views/industry.py", "산업 대시보드"))

    # 2·3. 성장·수익성: 산업 집계와 같은 기준(부문 실적이 있으면 부문)으로, 자기 자신을 뺀 같은 그룹과 비교
    mine_f = industry.company_frame(d)
    others = [industry.company_frame(data[c["name"]]) for c in cfg["companies"]
              if c["group"] == group and c["name"] != name and c["name"] in data
              and not data[c["name"]]["quarterly"].empty]
    period = mine_f["revenue"].dropna().index[-1] if mine_f["revenue"].notna().any() else q.index[-1]
    base = f"{int(period[:4]) - 1}{period[4:]}"
    seg_note = " (부문 기준)" if comp.get("business_segment") else ""

    def _yoy_of(f):
        r1, r0 = f["revenue"].get(period), f["revenue"].get(base)
        return (r1, r0) if pd.notna(r1) and pd.notna(r0) and r0 > 0 else (None, None)

    my_r1, my_r0 = _yoy_of(mine_f)
    pairs = [p for p in (_yoy_of(f) for f in others) if p[0] is not None]
    if my_r1 is not None and pairs:
        my_yoy = my_r1 / my_r0 - 1
        peer_yoy = sum(p[0] for p in pairs) / sum(p[1] for p in pairs) - 1
        st_ = GOOD if my_yoy >= peer_yoy else WATCH
        items.append(Item("성장", st_, "같은 그룹 경쟁사보다 빠르게 성장" if st_ == GOOD else "같은 그룹 경쟁사보다 느리게 성장",
                          f"매출 YoY {_pct(my_yoy)}{seg_note} vs {group} 경쟁사 {len(pairs)}곳 합산 {_pct(peer_yoy)} ({period})",
                          "views/company.py", "기업 분석"))
    else:
        items.append(Item("성장", NA, "성장 비교 불가", "전년 동기 데이터 부족", "views/company.py", "기업 분석"))

    my_rev, my_op = mine_f["revenue"].get(period), mine_f["op"].get(period)
    peer_rev = sum(f["revenue"].get(period) for f in others if pd.notna(f["revenue"].get(period)) and pd.notna(f["op"].get(period)))
    peer_op = sum(f["op"].get(period) for f in others if pd.notna(f["revenue"].get(period)) and pd.notna(f["op"].get(period)))
    if pd.notna(my_rev) and pd.notna(my_op) and my_rev > 0 and peer_rev > 0:
        my_opm, peer_opm = my_op / my_rev, peer_op / peer_rev
        st_ = GOOD if my_opm >= peer_opm else WATCH
        items.append(Item("수익성", st_, "같은 그룹 경쟁사보다 수익성 높음" if st_ == GOOD else "같은 그룹 경쟁사보다 수익성 낮음",
                          f"영업이익률 {my_opm*100:.1f}%{seg_note} vs {group} 경쟁사 합산 {peer_opm*100:.1f}% ({period})",
                          "views/company.py", "기업 분석"))

    # 4. 밸류에이션 (정보): ROE 대비 PBR 이 그룹보다 높은가
    row = snap.set_index("기업").loc[name] if name in set(snap["기업"]) else None
    # 그룹이 3곳 안팎이라 중앙값은 한 회사 값이 되어 버린다 → 자기 자신을 뺀 경쟁사 평균
    peers = snap[(snap["그룹"] == group) & (snap["기업"] != name)].dropna(subset=["PBR", "ROE"])
    peers = peers[peers["ROE"] > 0]
    if row is not None and pd.notna(row.get("PBR")) and pd.notna(row.get("ROE")) and row["ROE"] > 0 and len(peers) >= 1:
        mine = row["PBR"] / (row["ROE"] * 100)
        avg = (peers["PBR"] / (peers["ROE"] * 100)).mean()
        side = ("ROE 대비 PBR이 경쟁사와 비슷" if abs(mine / avg - 1) <= 0.10 else
                "ROE 대비 PBR이 경쟁사보다 낮음" if mine < avg else "ROE 대비 PBR이 경쟁사보다 높음")
        items.append(Item("밸류에이션", INFO, side,
                          f"PBR {row['PBR']:.1f}배 · ROE {row['ROE']*100:.1f}% → ROE 1%p당 PBR {mine:.3f} "
                          f"(경쟁사 {len(peers)}곳 평균 {avg:.3f}) · PER {row['PER']:.1f}배" if pd.notna(row.get("PER")) else
                          f"PBR {row['PBR']:.1f}배 · ROE {row['ROE']*100:.1f}% (경쟁사 {len(peers)}곳 평균 대비 비교)",
                          "views/company.py", "기업 분석"))
    else:
        items.append(Item("밸류에이션", NA, "비교 불가", "주가·ROE 데이터 부족 또는 적자", "views/company.py", "기업 분석"))

    # 4-1. 업황 민감도 × 현재 국면
    if sens is not None and sens.beta is not None and sens.level not in ("판단불가",):
        weak_phase = not ph.startswith(("확장", "회복")) and ph != "판단불가"
        strong = sens.level.startswith("뚜렷")
        st_ = WATCH if (weak_phase and strong) else INFO
        head = f"{sens.driver.split(' (')[0]}에 {sens.level}"
        ev = f"동인 10%p당 매출 성장률 {sens.beta*10:+.1f}%p · 상관계수 {sens.r:.2f} · 표본 {sens.n}개 분기"
        if weak_phase and strong:
            ev += f" · 현재 국면 '{ph}'이라 업황 둔화의 영향을 크게 받을 수 있음"
        items.append(Item("업황 민감도", st_, head, ev, "views/industry.py", "산업 대시보드 ⑤"))

    # 5. 현금흐름 유형
    t, desc, signs = classify(last.get("cfo_ttm"), last.get("cfi_ttm"), last.get("cff_ttm"))
    items.append(Item("현금흐름", CF_STATUS.get(t, NA), f"현금흐름 유형: {t}", f"영업/투자/재무 {signs} · {desc}",
                      "views/company.py", "기업 분석"))

    # 6. 이익의 질
    cs = quality.checks(d, model, orders, note_nums)
    warn = [c for c in cs if c.status == "경고"]
    watch = [c for c in cs if c.status == "주의"]
    st_ = BAD if len(warn) >= 2 else (WATCH if warn or watch else GOOD)
    items.append(Item("이익의 질", st_,
                      "이익의 질 양호" if st_ == GOOD else f"경고 {len(warn)}개 · 주의 {len(watch)}개",
                      " · ".join(f"{quality.ICON[c.status]}{c.name}" for c in warn + watch) or "모든 점검 항목 양호",
                      "views/quality.py", "이익의 질"))

    # 7. 감사: 의견 + 핵심감사사항과 수치 경고의 겹침
    au = quality.audit_summary(audit_df) if audit_df is not None else {"available": False}
    if au.get("available"):
        by_key = {c.key: c for c in cs}
        overlaps = []
        for k in au["kams"]:
            for chk in quality.kam_checks(k, by_key):
                if chk.status in ("주의", "경고"):
                    overlaps.append(f"'{k['text']}' ↔ {chk.name}")
        if au["modified"]:
            st_, head = BAD, "변형 감사의견"
        elif overlaps:
            st_, head = WATCH, "감사인 주목 계정에서 수치 이상"
        else:
            st_, head = GOOD, "적정의견, 겹치는 경고 없음"
        ev = ", ".join(au["opinions"]) + (" · 감사인 교체" if au["auditor_changed"] else "")
        if overlaps:
            ev += " · " + "; ".join(overlaps)
        items.append(Item("감사", st_, head, ev, "views/quality.py", "이익의 질"))
    else:
        items.append(Item("감사", NA, "감사의견 확인 불가", "데이터 없음", "views/filings.py", "사업·감사보고서"))

    # 8. 최근 이상치 (최근 2개 분기)
    recent = [a for a in anomaly.detect(q) if a.period in set(q.index[-2:])]
    if recent:
        serious = any(a.kind in ("restated", "cash") for a in recent)
        items.append(Item("이상치", WATCH if serious else INFO,
                          f"최근 2개 분기 이상치 {len(recent)}건",
                          " · ".join(f"{a.period} {anomaly.KINDS[a.kind]}" for a in recent),
                          "views/anomaly.py", "이상치 추적"))
    else:
        items.append(Item("이상치", GOOD, "최근 2개 분기 이상치 없음", "감지 기준은 이상치 추적 페이지 참고",
                          "views/anomaly.py", "이상치 추적"))
    return items
