"""정기보고서 'II. 사업의 내용 > 원재료 및 생산설비 > 생산능력, 생산실적, 가동률'에서 가동률을 뽑는다.

형식이 제각각이라 세 가지를 순서대로 시도한다.
1) '(가동률)' 뒤의 표에서 % 값
2) 문장 속 '가동률 … 00%'
3) 공개하지 않는다는 설명 문구 (주문생산이라 의미 없음 등)
"""
from __future__ import annotations

import re

_PCT_RE = re.compile(r"^\(?\s*(\d{1,3}(?:\.\d+)?)\s*%?\s*\)?$")
_SENT_RE = re.compile(r"가동[률율][^.。%]{0,30}?(\d{1,3}(?:\.\d+)?)\s*%")
_OMIT_RE = re.compile(r"생략|의미가 없|무의미|어렵|곤란|기재하지|산출하지|산출이 어려|부적합|주문생산")
_UTIL = ("가동률", "가동율")  # 회사에 따라 '가동율'로도 씀


def _has_util(s: str) -> bool:
    return any(u in s for u in _UTIL)


def _label(cells: list[str]) -> str:
    """숫자가 아닌 칸만 이어 붙여 항목 이름으로."""
    return " ".join(c for c in cells if c and not re.fullmatch(r"[\d,.\s%()\-]+", c))[:30]


def _around(text: str, words: tuple[str, ...], width: int = 260) -> str:
    """긴 문단에서 해당 단어 주변만 잘라 보여준다."""
    i = min((text.find(w) for w in words if w in text), default=0)
    s = max(0, i - 20)
    return ("…" if s else "") + text[s:s + width] + ("…" if len(text) > s + width else "")


def _pct(s: str) -> float | None:
    m = _PCT_RE.match(s.strip())
    if not m:
        return None
    v = float(m.group(1))
    return v if 0 < v <= 150 else None


def extract(blocks: list[tuple]) -> dict:
    """-> {'status': 'ok', 'items': [(항목, %)], 'period': 열 이름, 'note': str}
         | {'status': '미공시', 'note': str} | {'status': '없음', 'note': str}"""
    note = ""
    # 1) '(가동률)' 표시 뒤의 표, 또는 머리글에 '가동률'이 있는 표 (회사에 따라 여러 개 → 모두 모은다)
    after_mark = False
    all_items: list[tuple[str, float]] = []
    period_found = ""
    for b in blocks:
        if b[0] == "p":
            t = b[1]
            if _has_util(t) and ("(가동률)" in t or "[가동률]" in t or t.strip().startswith(_UTIL)
                                 or t.strip().endswith(_UTIL)):
                after_mark = True
            if _has_util(t) and not note:
                note = _around(t, _UTIL)
            continue
        tb = b[1]
        header = tb["header"]
        has_col = any(_has_util(h) for h in header)
        if not (after_mark or has_col):
            continue
        items, period = [], ""
        # 시간 기준(가동 가능 시간 대비 실제 가동 시간)이면 표시 — 24시간 가동 공장은 늘 100%에 가깝다
        basis = " (시간 기준)" if any("시간" in h for h in header) else ""
        # 머리글에 가동률 열이 있으면 그 열, 아니면 첫 번째로 %가 나오는 열(당기)
        col = next((j for j, h in enumerate(header) if _has_util(h)), None)
        for r in tb["rows"]:
            cells = r
            if col is not None and col < len(cells):
                v = _pct(cells[col])
                if v is not None:
                    items.append(((_label(cells[:col]) or "전체") + basis, v))
                continue
            for j, c in enumerate(cells):
                v = _pct(c)
                # 가동률 열이 없는 표에서는 '%'가 붙은 값만 인정 (다른 표의 작은 숫자를 오인하지 않도록)
                if v is not None and "%" in c:
                    items.append(((_label(cells[:j]) or "전체") + basis, v))
                    period = header[j] if j < len(header) else ""
                    break
        all_items.extend(items)
        period_found = period_found or period
        after_mark = False
    if all_items:
        return {"status": "ok", "items": all_items[:10], "period": period_found, "note": note[:300]}
    # 2) 문장
    for b in blocks:
        if b[0] == "p":
            m = _SENT_RE.search(b[1])
            if m:
                return {"status": "ok", "items": [("평균", float(m.group(1)))], "period": "",
                        "note": _around(b[1], _UTIL)}
    # 3) 공개하지 않는다는 설명 (가동률 또는 생산능력을 산출·기재하지 않는다는 문구)
    for words in (_UTIL, ("생산능력",)):
        for b in blocks:
            if b[0] == "p" and any(w in b[1] for w in words) and _OMIT_RE.search(b[1]):
                return {"status": "미공시", "note": _around(b[1], words, 320)}
    return {"status": "없음", "note": note[:300] or "가동률 정보를 찾지 못했습니다."}
