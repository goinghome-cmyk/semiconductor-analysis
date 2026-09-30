"""DART 원문 요소 -> 구조화된 블록(문단/표) -> 화면용 HTML·AI용 텍스트.

DART 원문 특징:
- 문단도 테두리 없는(BORDER=0) 1~2칸짜리 표(<TE> 셀)로 들어 있는 경우가 많다 → 문단으로 바꾼다.
- 실제 표는 여러 줄 머리글(TH)과 셀 병합(ROWSPAN/COLSPAN)이 많다 → 격자로 펼친 뒤 머리글을 합친다.
"""
from __future__ import annotations

import html
import re

from bs4 import NavigableString, Tag

_SECTION_RE = re.compile(r"^section-\d$")
_NUM_RE = re.compile(r"^[\(\-△▲]?\s*[\d,]+(\.\d+)?\s*\)?%?$")
_BLANK = {"", "　", "-"}
_SUBHEAD_RE = re.compile(r"^(\(?[가-하]\)|[가-하]\.|\(\d{1,2}\)|\d{1,2}\)|[①-⑳]|[IVX]+\.)\s*\S")
MAX_TABLE_ROWS = 400

# 블록: ("p", 문장) | ("table", {"header": [...], "rows": [[...]], "caption": ""})


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("　", " ")).strip()


def _span(cell: Tag, attr: str) -> int:
    try:
        return max(1, int(cell.get(attr, 1)))
    except (TypeError, ValueError):
        return 1


def _grid(table: Tag) -> tuple[list[list[str]], list[bool]]:
    """ROWSPAN/COLSPAN 을 펼친 격자와, 행별 '머리글 행' 여부."""
    grid: list[list[str]] = []
    is_head: list[bool] = []
    pending: dict[tuple[int, int], str] = {}
    trs = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
    for r, tr in enumerate(trs):
        cells = tr.find_all(["td", "th", "te", "tu"], recursive=False)
        row: list[str] = []
        c = 0
        for cell in cells:
            while (r, c) in pending:
                row.append(pending.pop((r, c)))
                c += 1
            text = _clean(cell.get_text(" "))
            head = cell.name == "th"
            cs, rs = _span(cell, "colspan"), _span(cell, "rowspan")
            for k in range(cs):
                # 머리글은 병합 범위 전체에 같은 이름을, 본문은 첫 칸에만
                val = text if (k == 0 or head) else ""
                row.append(val)
                for rr in range(1, rs):
                    pending[(r + rr, c)] = text if head else ""
                c += 1
        while (r, c) in pending:
            row.append(pending.pop((r, c)))
            c += 1
        grid.append(row)
        in_thead = tr.find_parent("thead") is not None
        is_head.append(in_thead or (bool(cells) and all(x.name == "th" for x in cells)))
    width = max((len(r) for r in grid), default=0)
    grid = [r + [""] * (width - len(r)) for r in grid]
    return grid, is_head


def _flatten_header(rows: list[list[str]]) -> list[str]:
    """여러 줄 머리글 -> 열 이름 하나. 위에서 아래로 겹치지 않는 이름을 이어 붙이되 마지막 2단계만."""
    if not rows:
        return []
    stacks = []
    for j in range(len(rows[0])):
        labels = []
        for r in rows:
            v = r[j]
            if v and v not in _BLANK and (not labels or labels[-1] != v):
                labels.append(v)
        stacks.append(labels)
    # 기본은 가장 아래 단계 이름만. 같은 이름이 여러 열에 있으면,
    # 그 열들 사이에서 서로 달라지는 가장 가까운 윗 단계 이름을 붙여 구분
    # (예: 영업부문 · DX 부문 / 내부거래 조정 등 · DX 부문, DX 부문 · 3개월 / DX 부문 · 누적).
    last = [s[-1] if s else "" for s in stacks]
    out = list(last)
    for name in set(last):
        cols = [j for j, n in enumerate(last) if n == name]
        if not name or len(cols) < 2:
            continue
        depth = min(len(stacks[j]) for j in cols)
        for k in range(2, depth + 1):
            if len({stacks[j][-k] for j in cols}) > 1:
                for j in cols:
                    out[j] = f"{stacks[j][-k]} · {name}"
                break
    return out


def _table_blocks(table: Tag) -> list[tuple]:
    grid, is_head = _grid(table)
    if not grid:
        return []
    # 테두리 없는 표 = 레이아웃용 → 문단으로
    border = str(table.get("border", "1")).strip()
    if border == "0":
        out = []
        for row in grid:
            parts = [c for c in row if c not in _BLANK]
            # 병합으로 같은 값이 반복되면 한 번만
            dedup = [p for i, p in enumerate(parts) if i == 0 or p != parts[i - 1]]
            if dedup:
                out.append(("p", "  ".join(dedup)))
        return out

    # 빈 열 제거
    keep = [j for j in range(len(grid[0])) if any(r[j] not in _BLANK for r in grid)]
    grid = [[r[j] for j in keep] for r in grid]
    n_head = 0
    while n_head < len(grid) and is_head[n_head]:
        n_head += 1
    header = _flatten_header(grid[:n_head]) if n_head else []
    body = [r for r in grid[n_head:] if any(c not in _BLANK for c in r)]
    if not header and not body:
        return []
    if not header and body and len(body) > 1:
        # TH 가 없는 표: 첫 행이 숫자가 아니면 머리글로 취급
        if not any(_NUM_RE.match(c) for c in body[0] if c):
            header, body = body[0], body[1:]
    return [("table", {"header": header, "rows": body})]


def blocks(node: Tag, top: bool = True) -> list[tuple]:
    """섹션 요소 -> 블록 목록 (하위 섹션 제외, 문서 순서 유지).
    섹션 바로 아래 TITLE 은 섹션 제목이라 건너뛰고, TABLE-GROUP 안의 TITLE(주석 번호 제목 등)은 문단으로 남긴다."""
    out: list[tuple] = []
    for el in node.children:
        if isinstance(el, NavigableString):
            t = _clean(str(el))
            if t:
                out.append(("p", t))
            continue
        if not isinstance(el, Tag):
            continue
        name = el.name
        if (name == "title" and top) or name in ("pgbrk", "colgroup", "image", "img") or _SECTION_RE.match(name):
            continue
        if name == "table":
            out.extend(_table_blocks(el))
        elif el.find(["table", "title", re.compile(r"^section-\d$")]) is not None:
            # TABLE-GROUP, LIBRARY 등 컨테이너. LIBRARY 는 하위 섹션을 감싸므로 섹션 수준 그대로.
            out.extend(blocks(el, top=top and name == "library"))
        else:
            t = _clean(el.get_text(" "))
            if t:
                out.append(("p", t))
    # 같은 문단이 연속으로 반복되면 하나만
    dedup = []
    for b in out:
        if not (dedup and b[0] == "p" and dedup[-1] == b):
            dedup.append(b)
    return dedup


# ------------------------------------------------------------------ 출력
def to_text(bl: list[tuple]) -> str:
    """AI 입력용 텍스트. 표는 머리글 + '|' 구분 행."""
    lines = []
    for kind, v in bl:
        if kind == "p":
            lines.append(v)
        else:
            if v["header"]:
                lines.append(" | ".join(v["header"]))
            lines.extend(" | ".join(r) for r in v["rows"])
            lines.append("")
    return "\n".join(lines).strip()


CSS = """
<style>
.dart-doc {font-size: 0.92rem; line-height: 1.65;}
.dart-doc p {margin: 0 0 0.55rem 0;}
.dart-doc p.sub {font-weight: 700; margin-top: 0.9rem;}
.dart-doc .h {font-weight: 700; font-size: 1.02rem; margin: 1.2rem 0 0.5rem 0; padding-bottom: 0.2rem;
              border-bottom: 1px solid rgba(128,128,128,0.35);}
.dart-doc .tbl {overflow-x: auto; margin: 0.3rem 0 1rem 0; -webkit-overflow-scrolling: touch;}
.dart-doc table {border-collapse: collapse; font-size: 0.82rem; min-width: 60%;}
.dart-doc th, .dart-doc td {border: 1px solid rgba(128,128,128,0.35); padding: 3px 8px;
                            vertical-align: top; white-space: nowrap;}
.dart-doc th {background: rgba(128,128,128,0.12); font-weight: 600; text-align: center;}
.dart-doc td.num {text-align: right; font-variant-numeric: tabular-nums;}
.dart-doc td.long {white-space: normal; min-width: 16rem; max-width: 36rem;}
.dart-doc .more {color: gray; font-size: 0.8rem;}
</style>
"""


def _cell(v: str) -> str:
    cls = "num" if _NUM_RE.match(v) else ("long" if len(v) > 30 else "")
    return f'<td class="{cls}">{html.escape(v)}</td>'


def _bullets(v: str) -> list[str] | None:
    """'…포함됩니다.ㆍ항목ㆍ항목' 처럼 문장 끝 뒤에 붙은 나열만 목록으로 나눈다.
    '국내ㆍ외'처럼 짧은 단어 사이의 가운뎃점은 나누지 않는다(앞 단어·뒷 단어가 모두 2글자 이하면 다시 붙임)."""
    if v.count("ㆍ") < 2 or not re.search(r"[.:]\s*ㆍ", v):
        return None
    pieces = v.split("ㆍ")
    merged = [pieces[0]]
    for p in pieces[1:]:
        first = p.split(" ", 1)[0]
        last = merged[-1].rsplit(" ", 1)[-1]
        if len(merged) > 1 and len(first) <= 2 and len(last) <= 2:
            merged[-1] += "ㆍ" + p
        else:
            merged.append(p)
    merged = [m.strip() for m in merged]
    return merged if len(merged) >= 3 else None


def to_html(bl: list[tuple], heading: str | None = None) -> str:
    parts = ['<div class="dart-doc">']
    if heading:
        parts.append(f'<div class="h">{html.escape(heading)}</div>')
    for kind, v in bl:
        if kind == "p":
            if v.startswith("## "):
                parts.append(f'<div class="h">{html.escape(v[3:])}</div>')
            elif (items := _bullets(v)):
                head, rest = items[0], items[1:]
                parts.append(f"<p>{html.escape(head)}</p>" if head.strip() else "")
                parts.append("<ul>" + "".join(f"<li>{html.escape(i)}</li>" for i in rest) + "</ul>")
            else:
                is_sub = len(v) <= 50 and (_SUBHEAD_RE.match(v) or v.endswith(("에 대한 공시", "에 대한 기술"))
                                           or (v.startswith("[") and v.endswith("]")))
                cls = ' class="sub"' if is_sub else ""
                parts.append(f"<p{cls}>{html.escape(v)}</p>")
            continue
        rows = v["rows"]
        parts.append('<div class="tbl"><table>')
        if v["header"]:
            parts.append("<thead><tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in v["header"])
                         + "</tr></thead>")
        parts.append("<tbody>")
        for r in rows[:MAX_TABLE_ROWS]:
            parts.append("<tr>" + "".join(_cell(c) for c in r) + "</tr>")
        parts.append("</tbody></table></div>")
        if len(rows) > MAX_TABLE_ROWS:
            parts.append(f'<p class="more">… 표가 길어 {MAX_TABLE_ROWS}행까지만 표시 (전체 {len(rows)}행)</p>')
    parts.append("</div>")
    return "".join(parts)


def split_note_blocks(bl: list[tuple]) -> list[tuple[str, list[tuple]]]:
    """주석 블록을 '1. 일반사항', '2. …' 제목 문단 기준으로 나눈다 (번호 1~2개 누락 허용)."""
    notes: list[tuple[str, list[tuple]]] = []
    expected = 1
    title, buf = "머리말", []
    for b in bl:
        if b[0] == "p":
            m = re.match(r"^\s*(\d{1,2})\s*[.)]\s*([^\d\s].{0,60})$", b[1])
            if m and expected <= int(m.group(1)) <= expected + 2:
                if buf:
                    notes.append((title, buf))
                n = int(m.group(1))
                title, buf = f"{n}. {m.group(2).strip()}", []
                expected = n + 1
                continue
        buf.append(b)
    if buf:
        notes.append((title, buf))
    return [(t, b) for t, b in notes if b]
