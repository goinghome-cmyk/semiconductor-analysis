"""구글 뉴스 RSS 검색 (API 키 불필요)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import pandas as pd
import requests

RSS_URL = "https://news.google.com/rss/search"


def search_range(query: str, start: str, end: str, limit: int = 30) -> pd.DataFrame:
    """기간 지정 검색 (YYYY-MM-DD). 과거 이상치 시점의 뉴스를 찾을 때."""
    return _fetch(f"{query} after:{start} before:{end}", limit)


def search(query: str, days: int = 30, limit: int = 40) -> pd.DataFrame:
    return _fetch(f"{query} when:{days}d", limit)


def _fetch(q: str, limit: int) -> pd.DataFrame:
    resp = requests.get(
        RSS_URL,
        params={"q": q, "hl": "ko", "gl": "KR", "ceid": "KR:ko"},
        timeout=15,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    rows = []
    for item in root.iter("item"):
        source = item.findtext("source") or ""
        title = item.findtext("title") or ""
        suffix = f" - {source}"
        if source and title.endswith(suffix):
            title = title[: -len(suffix)]
        try:
            published = parsedate_to_datetime(item.findtext("pubDate"))
        except (TypeError, ValueError):
            published = None
        rows.append({"제목": title, "언론사": source, "날짜": published,
                     "링크": item.findtext("link")})
    df = pd.DataFrame(rows, columns=["제목", "언론사", "날짜", "링크"])
    if df.empty:
        return df
    df["날짜"] = pd.to_datetime(df["날짜"], utc=True).dt.tz_convert("Asia/Seoul")
    df = df.drop_duplicates("제목").sort_values("날짜", ascending=False)
    return df.head(limit).reset_index(drop=True)
