"""증권사 리포트 PDF -> 텍스트 -> (Claude) 추정치·전망 추출, 추정치 저장소."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
from pypdf import PdfReader

ESTIMATES_PATH = Path(__file__).resolve().parent.parent / "data" / "estimates.csv"
ESTIMATE_COLUMNS = ["기업", "증권사", "발행일", "연도", "매출액(억원)", "영업이익(억원)",
                    "EPS(원)", "목표주가(원)", "투자의견"]

_num = {"anyOf": [{"type": "number"}, {"type": "null"}]}
_str = {"anyOf": [{"type": "string"}, {"type": "null"}]}

REPORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["broker", "report_date", "title", "report_type", "company", "rating",
                 "target_price", "key_points", "industry_outlook", "risks", "estimates"],
    "properties": {
        "broker": _str,
        "report_date": {**_str, "description": "YYYY-MM-DD"},
        "title": _str,
        "report_type": {"type": "string", "enum": ["기업", "산업"]},
        "company": {**_str, "description": "기업 리포트면 대상 기업명, 산업 리포트면 null"},
        "rating": _str,
        "target_price": {**_num, "description": "원 단위"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "industry_outlook": {"type": "string", "description": "산업 전망 요약. 없으면 빈 문자열"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "estimates": {
            "type": "array",
            "description": "연간 실적 및 추정치 표. 과거 실적(A)과 추정(E) 모두 포함",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["company", "year", "is_estimate", "revenue", "operating_profit", "eps"],
                "properties": {
                    "company": _str,
                    "year": {"type": "integer"},
                    "is_estimate": {"type": "boolean"},
                    "revenue": {**_num, "description": "억원 단위로 환산"},
                    "operating_profit": {**_num, "description": "억원 단위로 환산"},
                    "eps": {**_num, "description": "원 단위"},
                },
            },
        },
    },
}

EXTRACT_INSTRUCTION = (
    "위 자료는 증권사 리포트입니다. 스키마에 맞게 정보를 추출하세요. "
    "금액은 표의 단위(십억원, 백만원 등)를 확인해 매출·영업이익은 억원, EPS·목표주가는 원으로 환산하세요. "
    "표에 없는 값은 null 로 두고 추측하지 마세요. key_points 는 핵심 논지 3~5개, risks 는 리포트가 언급한 위험 요인입니다."
)


def pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages, 1):
        pages.append(f"[p.{i}]\n{page.extract_text() or ''}")
    return "\n\n".join(pages)


NOTES_PATH = ESTIMATES_PATH.parent / "report_notes.json"


def load_report_notes() -> list[dict]:
    if NOTES_PATH.exists():
        return json.loads(NOTES_PATH.read_text(encoding="utf-8"))
    return []


def add_report_note(data: dict) -> None:
    notes = [n for n in load_report_notes()
             if (n.get("broker"), n.get("report_date"), n.get("title"))
             != (data.get("broker"), data.get("report_date"), data.get("title"))]
    notes.append(data)
    NOTES_PATH.parent.mkdir(parents=True, exist_ok=True)
    NOTES_PATH.write_text(json.dumps(notes, ensure_ascii=False, indent=1), encoding="utf-8")


def load_estimates() -> pd.DataFrame:
    if ESTIMATES_PATH.exists():
        df = pd.read_csv(ESTIMATES_PATH, dtype={"연도": "Int64"})
        return df.reindex(columns=ESTIMATE_COLUMNS)
    return pd.DataFrame(columns=ESTIMATE_COLUMNS)


def save_estimates(df: pd.DataFrame) -> None:
    ESTIMATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.reindex(columns=ESTIMATE_COLUMNS).to_csv(ESTIMATES_PATH, index=False, encoding="utf-8-sig")


def estimates_from_extraction(data: dict) -> pd.DataFrame:
    rows = []
    for e in data.get("estimates", []):
        if not e.get("is_estimate"):
            continue
        rows.append({
            "기업": e.get("company") or data.get("company"),
            "증권사": data.get("broker"),
            "발행일": data.get("report_date"),
            "연도": e.get("year"),
            "매출액(억원)": e.get("revenue"),
            "영업이익(억원)": e.get("operating_profit"),
            "EPS(원)": e.get("eps"),
            "목표주가(원)": data.get("target_price"),
            "투자의견": data.get("rating"),
        })
    return pd.DataFrame(rows, columns=ESTIMATE_COLUMNS)


def consensus(df: pd.DataFrame, company: str) -> pd.DataFrame:
    """기업별·연도별 추정치 평균 (여러 증권사)."""
    sub = df[df["기업"] == company]
    if sub.empty:
        return pd.DataFrame()
    num_cols = ["매출액(억원)", "영업이익(억원)", "EPS(원)", "목표주가(원)"]
    sub = sub.copy()
    sub[num_cols] = sub[num_cols].apply(pd.to_numeric, errors="coerce")
    g = sub.groupby("연도")
    out = g[num_cols].mean()
    out["리포트 수"] = g.size()
    return out
