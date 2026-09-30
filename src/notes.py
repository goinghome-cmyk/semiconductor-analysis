"""재무제표 주석에서 숫자 뽑기: 재고자산 평가충당금, 매출채권 손실(대손)충당금.

재무상태표에는 충당금을 뺀 '순액'만 나오므로, 회사가 재고·외상을 얼마나 보수적으로 평가했는지는
주석의 '총액 · 충당금 · 장부금액' 표를 봐야 알 수 있다. 사업보고서 주석은 대부분 XBRL 표준 표라
열 이름(총장부금액·평가충당금·손상차손누계액)이 회사마다 비슷하다.
"""
from __future__ import annotations

import re

UNITS = {"원": 1, "천원": 1e3, "백만원": 1e6, "억원": 1e8}
_UNIT_RE = re.compile(r"단위\s*[:：]\s*(백만원|천원|억원|원)")


def _num(s) -> float | None:
    t = str(s or "").replace(",", "").strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?", t):
        return None
    v = float(t)
    return -v if neg else v


def _tables(blocks: list):
    """(기간 '당기'/'전기', 단위 배수, 표) 를 차례로. 기간·단위는 표 바로 앞 문단에서 읽는다."""
    period, unit = "당기", 1
    for kind, b in blocks:
        if kind == "p":
            head = b[:40]
            if head.startswith("전기"):
                period = "전기"
            elif head.startswith("당기"):
                period = "당기"
            m = _UNIT_RE.search(b[:60])
            if m:
                unit = UNITS[m.group(1)]
        elif kind == "table":
            yield period, unit, b


def _col(header: list[str], *keys: str) -> int | None:
    """keys 중 하나를 포함하는 열. 연령분석표처럼 '총장부금액 · 미연체' 같은 다단 열은 제외."""
    for i, h in enumerate(header):
        h = str(h)
        if "·" not in h and any(k in h for k in keys):
            return i
    return None


def _name(row: list[str], first_val: int) -> tuple[str, str]:
    """행 머리(숫자 열 앞의 칸들) → (상위 이름, 하위 이름)."""
    cells = [str(c).strip() for c in row[:min(first_val, 2)]]
    cells = [c if _num(c) is None else "" for c in cells] + ["", ""]
    return cells[0], cells[1]


def _pair(row, gi, ai, unit) -> dict | None:
    g, a = _num(row[gi]) if gi < len(row) else None, _num(row[ai]) if ai < len(row) else None
    if g is None or g <= 0:
        return None
    a = abs(a or 0)
    return {"gross": g * unit, "allow": a * unit, "ratio": a / g}


# ------------------------------------------------------------------ 재고자산
def _inventory_table(table: dict, unit: float) -> dict | None:
    h = table["header"]
    gi = _col(h, "평가전", "취득원가", "총장부금액")
    ai = _col(h, "충당금")
    if gi is None or ai is None:
        return None
    first = min(gi, ai)
    rows = table["rows"]
    # 합계 행: '합계'·'총…'·(상위 '재고자산' + 하위 없음)
    for r in rows:
        top, sub = _name(r, first)
        label = (sub or top).replace(" ", "")
        if "합계" in label or label.startswith("총") or (top.replace(" ", "") == "재고자산" and not sub):
            p = _pair(r, gi, ai, unit)
            if p:
                return p
    # 합계 행이 없으면 품목을 더한다
    g = a = 0.0
    for r in rows:
        p = _pair(r, gi, ai, unit)
        if p:
            g, a = g + p["gross"], a + p["allow"]
    return {"gross": g, "allow": a, "ratio": a / g} if g > 0 else None


# ------------------------------------------------------------------ 매출채권
_REC_PRIORITY = (
    lambda n: n == "매출채권",
    lambda n: n == "유동매출채권",
    lambda n: "매출채권" in n and not any(k in n for k in ("기타", "장기", "비유동", "미수")),
)


def _receivable_table(table: dict, unit: float) -> dict | None:
    h = table["header"]
    gi = _col(h, "총장부금액")
    ai = _col(h, "손상차손누계액", "대손충당금", "손실충당금")
    if gi is None or ai is None:
        # 매출채권 표가 따로 없고 연령분석표('총장부금액 · 연체상태 합계')만 있는 회사(한미반도체 등)
        gi = next((i for i, x in enumerate(h) if "총장부금액" in str(x) and "합계" in str(x)), None)
        ai = next((i for i, x in enumerate(h) if "합계" in str(x)
                   and any(k in str(x) for k in ("손상차손누계액", "대손충당금", "손실충당금"))), None)
    if gi is None or ai is None:
        return None
    first = min(gi, ai)
    named = []
    for r in table["rows"]:
        top, sub = _name(r, first)
        p = _pair(r, gi, ai, unit)
        if p:
            named.append(((sub or top).replace(" ", ""), p))
    for rule in _REC_PRIORITY:
        for n, p in named:
            if rule(n):
                return p
    return None


def _scan(notes: list[tuple[str, list]], title_keys: tuple[str, ...], fn) -> dict | None:
    """제목에 title_keys 가 있는 주석부터, 없으면 모든 주석에서 당기·전기 값을 찾는다."""
    ordered = ([n for n in notes if any(k in n[0] for k in title_keys)]
               + [n for n in notes if not any(k in n[0] for k in title_keys)])
    for title, blocks in ordered:
        found = {}
        for period, unit, t in _tables(blocks):
            if period in found:
                continue
            v = fn(t, unit)
            if v:
                found[period] = v
            if len(found) == 2:
                break
        if "당기" in found:
            return {"cur": found["당기"], "prev": found.get("전기"), "note": title}
    return None


def extract(notes: list[tuple[str, list]]) -> dict:
    """notes: appdata.note_blocks() 결과 [(주석 제목, 블록)].
    반환: {'inventory': {cur, prev, note} | None, 'receivables': {...} | None}
    cur/prev = {'gross': 총액(원), 'allow': 충당금(원), 'ratio': 충당금/총액}"""
    return {
        "inventory": _scan(notes, ("재고자산",), _inventory_table),
        "receivables": _scan(notes, ("매출채권", "채권"), _receivable_table),
    }
