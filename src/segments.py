"""여러 사업을 하는 기업(예: 삼성전자)의 '부문별 보고' 주석에서 특정 부문 실적만 뽑는다.

정기보고서마다 원문을 받아야 하므로 추출 결과(작은 JSON)를 캐시한다.
원문 zip(보고서당 0.5MB 정도)도 남겨서, 파서를 고쳐도 DART 를 다시 부르지 않게 한다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from . import disclosure
from .dart import CACHE_DIR, DartClient, REPRT_FY, REPRT_H1, REPRT_Q1, REPRT_Q3

SEG_CACHE = CACHE_DIR / "segments"
_PERIOD_MONTH = {REPRT_Q1: "03", REPRT_H1: "06", REPRT_Q3: "09", REPRT_FY: "12"}
_NUM = re.compile(r"^\(?-?[\d,]+\)?$")
_GENERIC_HEADERS = {"영업부문", "내부거래 조정 등", "부문", "보고부문", "기업 전체 총계",
                    "3개월", "누적", "연결실체 내 내부거래조정", "지역", "중요한 조정사항"}


def _to_num(tok: str) -> float:
    neg = tok.startswith("(") or tok.startswith("-")
    v = float(tok.strip("()-").replace(",", ""))
    return -v if neg else v


def _parse_pipe_tables(note_text: str, keyword: str) -> dict | None:
    """옛 형식: '구 분 | DX 부문 | DS 부문 | ...' 표. 누적 표가 있으면 그것을, 없으면 첫 표를 쓴다."""
    lines = [l.strip() for l in note_text.splitlines()]
    tables = []  # (caption, unit, {행이름: [값...]}, 열 위치)
    caption, unit = "", 1e6
    i = 0
    while i < len(lines):
        l = lines[i]
        if "|" not in l:
            if l.startswith("("):
                if "단위" in l:
                    unit = 1e6 if "백만원" in l else (1e3 if "천원" in l else 1)
                else:
                    caption = l
            i += 1
            continue
        cells = [c.strip() for c in l.split("|")]
        col = next((j for j, c in enumerate(cells) if keyword in c), None)
        if col is None:
            i += 1
            continue
        rows = {}
        i += 1
        while i < len(lines) and "|" in lines[i]:
            r = [c.strip() for c in lines[i].split("|")]
            rows[r[0].replace(" ", "")] = r
            i += 1
        tables.append((caption, unit, rows, col))
    if not tables:
        return None
    cap, unit, rows, col = next((t for t in tables if "누적" in t[0]), tables[0])
    out = {}
    for key, labels in (("revenue", ("매출액", "영업수익", "매출")), ("op", ("영업이익",))):
        r = next((rows[k] for k in rows if k.startswith(labels)), None)
        if not r or len(r) <= col or not _NUM.match(r[col]):
            return None
        out[key] = _to_num(r[col]) * unit
    return out


def parse_segment(note_text: str, keyword: str) -> dict | None:
    if re.search(r"^구\s*분\s*\|", note_text, re.M):
        return _parse_pipe_tables(note_text, keyword)
    return _parse_cells(note_text, keyword)


def _parse_cells(note_text: str, keyword: str) -> dict | None:
    """부문별 보고 주석 텍스트 -> {'revenue': 누적, 'op': 누적} (원 단위).

    표가 셀 단위 줄로 풀려 있으므로: 첫 '당기/당분기/당반기' 블록에서
    부문 이름 순서를 읽고, '매출액'/'영업이익' 다음에 나오는 숫자 중 해당 위치를 고른다.
    """
    lines = [l.strip() for l in note_text.splitlines() if l.strip() and l.strip() != "　"]
    try:
        start = next(i for i, l in enumerate(lines) if "영업부문에 대한 공시" in l or l.startswith("영업부문 정보"))
    except StopIteration:
        start = 0
    block_end = next((i for i in range(start + 1, len(lines))
                      if re.match(r"^전(기|분기|반기)$", lines[i])), len(lines))
    block = lines[start:block_end]

    unit = 1e6 if any("백만원" in l for l in block[:10]) else (1e3 if any("천원" in l for l in block[:10]) else 1)
    try:
        first_metric = next(i for i, l in enumerate(block) if l.startswith(("매출", "영업수익")))
    except StopIteration:
        return None
    header = block[:first_metric]
    # 부문 이름 목록 = 키워드(예: 'DS 부문')가 들어간 줄 앞뒤로 이어진 이름들.
    # 머리글 구성은 보고서마다 달라서(보고부문 행 유무 등) 일반 머리글 단어를 만나면 멈춘다.
    k = next((i for i, l in enumerate(header) if keyword in l), None)
    if k is None:
        return None

    def is_name(l: str) -> bool:
        return (l not in _GENERIC_HEADERS and "(단위" not in l and "합계" not in l
                and not re.match(r"^(당|전)(기|분기|반기)", l))

    s = k
    while s > 0 and is_name(header[s - 1]):
        s -= 1
    pos = k - s
    has_cum = "누적" in header
    idx = pos * 2 + 1 if has_cum else pos

    out = {}
    for key, label in (("revenue", ("매출", "영업수익")), ("op", ("영업이익",))):
        try:
            i = next(j for j, l in enumerate(block) if l.startswith(label))
        except StopIteration:
            return None
        nums = []
        for l in block[i + 1:]:
            if _NUM.match(l):
                nums.append(_to_num(l))
            elif nums:
                break
        if len(nums) <= idx:
            return None
        out[key] = nums[idx] * unit
    return out


def _report_list(client: DartClient, corp_code: str, bgn: str, end: str) -> dict[str, str]:
    """'YYYY.MM' -> rcept_no (정기보고서, 최종본)."""
    data = client._get_json("list", {"corp_code": corp_code, "bgn_de": bgn, "end_de": end,
                                     "pblntf_ty": "A", "last_reprt_at": "Y", "page_count": "100"},
                            ttl=24 * 3600)
    out = {}
    for r in data.get("list", []):
        m = re.search(r"\((\d{4}\.\d{2})\)", r["report_nm"])
        if m and m.group(1) not in out:
            out[m.group(1)] = r["rcept_no"]
    return out


def segment_cumulative(client: DartClient, corp_code: str, periods: list[tuple[int, str]],
                       keyword: str) -> dict[tuple[int, str], dict]:
    """(연도, 보고서코드) 목록 -> 해당 부문 누적 매출/영업이익."""
    SEG_CACHE.mkdir(parents=True, exist_ok=True)
    years = [y for y, _ in periods]
    reports = _report_list(client, corp_code, f"{min(years)}0101", f"{max(years) + 1}1231")
    out = {}
    for y, rc in periods:
        rcept = reports.get(f"{y}.{_PERIOD_MONTH[rc]}")
        if not rcept:
            continue
        path = SEG_CACHE / f"{rcept}_{keyword}.json"
        if path.exists():
            val = json.loads(path.read_text(encoding="utf-8"))
        else:
            val = _extract_from_document(client, rcept, keyword, keep=True)
            path.write_text(json.dumps(val, ensure_ascii=False), encoding="utf-8")
        if val:
            out[(y, rc)] = val
    return out


def _extract_from_document(client: DartClient, rcept_no: str, keyword: str, keep: bool) -> dict | None:
    zip_path = client.cache_dir / f"doc_{rcept_no}.zip"
    existed = zip_path.exists()
    files = client.document(rcept_no)
    if not keep and not existed:
        zip_path.unlink(missing_ok=True)
    secs = disclosure.parse_sections(disclosure.main_document(files), keep=("재무에 관한 사항",))
    note = disclosure.find_section(secs, "연결재무제표 주석")
    if not note:
        return None
    for title, body in disclosure.split_notes(disclosure.full_text(note)):
        if "부문" in title:
            return parse_segment(body, keyword)
    return None


def segment_quarterly(cum: dict[tuple[int, str], dict]) -> pd.DataFrame:
    """누적 -> 분기 (같은 연도 직전 누적치를 빼기). index: 'YYYYQn'."""
    order = {REPRT_Q1: 1, REPRT_H1: 2, REPRT_Q3: 3, REPRT_FY: 4}
    rows = sorted(((y, order[rc], v) for (y, rc), v in cum.items()))
    prev: dict[int, tuple[int, dict]] = {}
    out = {}
    for y, q, v in rows:
        p = prev.get(y)
        if q == 1:
            val = v
        elif p and p[0] == q - 1:
            val = {k: v[k] - p[1][k] for k in ("revenue", "op")}
        else:
            val = None
        prev[y] = (q, v)
        if val:
            out[f"{y}Q{q}"] = val
    return pd.DataFrame(out).T
