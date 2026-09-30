"""정기보고서 'II. 사업의 내용 > 매출 및 수주상황'에서 수주잔고를 뽑는다.

표 형식은 회사마다 조금씩 다르지만 대체로
  품목 | 수주일자 | 납기 | 수주총액(수량·금액) | 기납품액(수량·금액) | 수주잔고(수량·금액)
이고, 표 바로 위 문단에 '(단위 : 백만원)'이 있다.
"""
from __future__ import annotations

import re

UNITS = {"원": 1, "천원": 1e3, "백만원": 1e6, "억원": 1e8, "십억원": 1e9}
_UNIT_RE = re.compile(r"단위\s*[:：]\s*([^\s,)]+)")
_NUM_RE = re.compile(r"^\(?-?[\d,]+(\.\d+)?\)?$")
TOTAL_ROWS = {"합계", "계", "총계", "합 계", "총 계"}


def _num(s: str) -> float | None:
    s = s.strip()
    if not _NUM_RE.match(s):
        return None
    neg = s.startswith("(") or s.startswith("-")
    v = float(s.strip("()-").replace(",", ""))
    return -v if neg else v


def _pick_col(header: list[str], word: str) -> int | None:
    cols = [j for j, h in enumerate(header) if word in h.replace(" ", "")]
    if not cols:
        return None
    money = [j for j in cols if "금액" in header[j]]
    return (money or cols)[-1]


def extract(blocks: list[tuple]) -> dict:
    """-> {'status': 'ok', 'backlog': 원, 'orders': 원|None, 'unit': str, 'note': str}
         | {'status': '미기재'|'없음', 'note': str}"""
    try:
        start = next(i for i, b in enumerate(blocks) if b[0] == "p" and "매출 및 수주" in b[1])
    except StopIteration:
        return {"status": "없음", "note": "'매출 및 수주상황' 섹션을 찾지 못했습니다."}
    unit_label, unit = "원", 1.0
    notes = []
    for b in blocks[start + 1:]:
        if b[0] == "p":
            t = b[1]
            if t.startswith("## "):
                break
            m = _UNIT_RE.search(t)
            if m:
                unit_label = m.group(1)
                unit = UNITS.get(unit_label, None) or 1.0
            if "수주" in t and ("※" in t or "기재" in t or "생략" in t):
                notes.append(t)
            continue
        tb = b[1]
        header = list(tb["header"] or (tb["rows"][0] if tb["rows"] else []))
        rows = tb["rows"] if tb["header"] else tb["rows"][1:]
        if not any("수주잔고" in h.replace(" ", "") for h in header):
            continue
        # 두 줄 머리글: 다음 행이 '수량/금액' 같은 하위 머리글이면 합친다 (병합 칸은 왼쪽 이름을 이어받음)
        if rows and any(("금액" in c.replace(" ", "") or "수량" in c.replace(" ", "")) for c in rows[0]) \
                and not any(_num(c) is not None for c in rows[0]):
            sub = rows[0]
            filled, prev = [], ""
            for h in header:
                prev = h or prev
                filled.append(prev)
            header = [f"{h} {s}".strip() for h, s in zip(filled, sub + [""] * (len(filled) - len(sub)))]
            rows = rows[1:]
            for s in sub:  # 칸 안에 '(단위: 원)'이 들어있는 경우
                m = _UNIT_RE.search(s)
                if m:
                    unit_label, unit = m.group(1), UNITS.get(m.group(1), 1.0)
        col = _pick_col(header, "수주잔고")
        ocol = _pick_col(header, "수주총액") or _pick_col(header, "수주금액")
        total = next((r for r in rows if r and r[0].replace(" ", "") in {x.replace(" ", "") for x in TOTAL_ROWS}), None)
        if total is not None and col < len(total) and _num(total[col]) is not None:
            bl = _num(total[col])
            od = _num(total[ocol]) if ocol is not None and ocol < len(total) else None
        else:
            vals = [_num(r[col]) for r in rows if col < len(r)]
            vals = [v for v in vals if v is not None]
            if not vals:
                # 수주잔고는 비워 두고 공시한 계약 금액만 적은 경우
                src = total if total is not None else None
                od = _num(src[ocol]) if src is not None and ocol is not None and ocol < len(src) else None
                if od is None and ocol is not None:
                    ov = [_num(r[ocol]) for r in rows if ocol < len(r)]
                    ov = [v for v in ov if v is not None]
                    od = sum(ov) if ov else None
                if od is not None:
                    return {"status": "부분", "orders": od * unit, "unit": unit_label,
                            "note": "수주잔고는 기재하지 않고, 공시한 공급계약 금액만 적었습니다. " + " ".join(notes)[:300]}
                continue
            bl = sum(vals)
            ov = [_num(r[ocol]) for r in rows if ocol is not None and ocol < len(r)]
            ov = [v for v in ov if v is not None]
            od = sum(ov) if ov else None
        if "달러" in unit_label or unit_label not in UNITS:
            return {"status": "단위확인", "backlog": bl, "orders": od, "unit": unit_label,
                    "note": f"원화가 아닌 단위({unit_label})라 금액 비교에서 제외했습니다."}
        return {"status": "ok", "backlog": bl * unit, "orders": od * unit if od is not None else None,
                "unit": unit_label, "note": " ".join(notes)[:400]}
    text = " ".join(notes)
    if notes:
        return {"status": "미기재", "note": text[:400]}
    return {"status": "없음", "note": "수주잔고 표를 찾지 못했습니다."}
