"""이상치 감지 → 원인 단서 수집 (분기보고서 원문 · 같은 시기 공시 · 같은 시기 뉴스).

판단은 하지 않고 '어디를 보면 되는지'를 좁혀준다. 기준은 모두 상수로 드러낸다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

TH_REV = 0.25      # 매출 전분기 대비 ±25%
TH_OPM = 0.10      # 영업이익률 전분기 대비 ±10%p
TH_INV = 0.30      # 재고 전분기 대비 +30%

KINDS = {
    "rev": "매출 급변",
    "margin": "이익률 급변",
    "inv": "재고 급증",
    "restated": "과거 실적 재작성",
    "cash": "흑자인데 현금 유출",
}

# 원문에서 찾을 단어 (이상치 종류별)
KEYWORDS = {
    "rev": ["매출", "감소", "증가", "수요", "판매", "출하", "고객", "가격", "수주", "전년", "전분기"],
    "margin": ["영업이익", "원가", "이익률", "수익성", "가동률", "판가", "비용", "손상", "충당", "일회성", "환율"],
    "inv": ["재고", "평가손실", "순실현", "재고자산", "수요"],
    "restated": ["중단영업", "분할", "매각", "처분", "재분류", "재작성", "소급", "비교표시", "종속기업", "양도"],
    "cash": ["영업활동", "현금흐름", "운전자본", "매출채권", "선급", "계약자산", "재고", "선수금"],
}
# 같은 시기 공시 중 눈여겨볼 제목 단어
EVENT_WORDS = ["분할", "합병", "매각", "양수", "양도", "처분", "취득", "공급계약", "판매", "유상증자",
               "전환사채", "감자", "소송", "영업정지", "정정", "손상", "투자", "시설", "타법인"]
NOISE_REPORTS = ("분기보고서", "반기보고서", "사업보고서", "소유상황보고서", "대량보유상황보고서",
                 "특정증권등소유", "기업설명회", "주식등의대량보유")


@dataclass
class Anomaly:
    period: str
    kind: str
    value: str
    size: float  # 정렬용 크기

    @property
    def label(self) -> str:
        return f"{self.period} · {KINDS[self.kind]} · {self.value}"


def detect(q: pd.DataFrame, last_n: int = 8) -> list[Anomaly]:
    q = q.copy()
    q["opm_chg"] = q["opm"] - q["opm"].shift(1)
    q["inv_qoq"] = q["inventory"] / q["inventory"].shift(1) - 1
    out = []
    for p, r in q.tail(last_n).iterrows():
        if pd.notna(r.get("rev_qoq")) and abs(r["rev_qoq"]) >= TH_REV:
            out.append(Anomaly(p, "rev", f"매출 전분기 대비 {r['rev_qoq']*100:+.0f}%", abs(r["rev_qoq"])))
        if pd.notna(r.get("opm_chg")) and abs(r["opm_chg"]) >= TH_OPM:
            out.append(Anomaly(p, "margin", f"영업이익률 {r['opm_chg']*100:+.1f}%p (→ {r['opm']*100:.1f}%)",
                               abs(r["opm_chg"]) * 2))
        if pd.notna(r.get("inv_qoq")) and r["inv_qoq"] >= TH_INV:
            out.append(Anomaly(p, "inv", f"재고 전분기 대비 {r['inv_qoq']*100:+.0f}%", r["inv_qoq"]))
        if bool(r.get("restated")):
            out.append(Anomaly(p, "restated", "공시된 3개월 매출과 누적 차이가 불일치", 1.0))
        if pd.notna(r.get("op")) and pd.notna(r.get("cfo")) and r["op"] > 0 and r["cfo"] < 0:
            out.append(Anomaly(p, "cash", f"영업이익 {r['op']/1e8:,.0f}억 · 영업CF {r['cfo']/1e8:,.0f}억", 0.8))
    return sorted(out, key=lambda a: (a.period, a.size), reverse=True)


def period_dates(period: str) -> tuple[date, date, str]:
    """'2025Q3' -> (분기 시작일, 분기 종료일, 보고서 기간 'YYYY.MM')."""
    y, qn = int(period[:4]), int(period[-1])
    start = date(y, 3 * qn - 2, 1)
    end = (date(y, 3 * qn, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return start, end, f"{y}.{3 * qn:02d}"


def rank_paragraphs(blocks: list[tuple], kind: str, top: int = 6) -> list[tuple[str, int]]:
    """블록 중 이상치 종류와 관련된 문단을 점수순으로. (문단, 점수)"""
    words = KEYWORDS[kind]
    scored = []
    section = ""
    for b in blocks:
        if b[0] != "p":
            continue
        t = b[1]
        if t.startswith("## "):
            section = t[3:]
            continue
        if not (30 <= len(t) <= 1500):
            continue
        hits = {w for w in words if w in t}
        if len(hits) < 2:
            continue
        score = len(hits) * 2 + min(len(re.findall(r"\d[\d,.]*\s*(%|억|조|백만)", t)), 5)
        scored.append((f"[{section}] {t}" if section else t, score))
    scored.sort(key=lambda x: -x[1])
    return scored[:top]


def highlight(text: str, kind: str) -> str:
    """마크다운 굵게로 키워드 강조."""
    for w in sorted(KEYWORDS[kind], key=len, reverse=True):
        text = text.replace(w, f"**{w}**")
    return text.replace("****", "")


def events(disc: pd.DataFrame) -> pd.DataFrame:
    """같은 시기 공시 목록에서 정기보고서·지분 공시 등 잡음을 빼고, 눈여겨볼 단어가 있으면 표시."""
    if disc is None or disc.empty:
        return pd.DataFrame()
    df = disc[~disc["report_nm"].str.contains("|".join(NOISE_REPORTS), na=False)].copy()
    df["주목"] = df["report_nm"].apply(lambda s: "⭐" if any(w in s for w in EVENT_WORDS) else "")
    df["날짜"] = pd.to_datetime(df["rcept_dt"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    df["링크"] = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + df["rcept_no"]
    return df[["주목", "날짜", "report_nm", "링크"]].rename(columns={"report_nm": "공시 제목"}) \
        .sort_values(["주목", "날짜"], ascending=[False, True]).reset_index(drop=True)


def clues(kind: str, paragraphs: list, ev: pd.DataFrame, headlines: list[str]) -> list[str]:
    """모은 자료에서 규칙으로 뽑은 단서 (결론이 아님)."""
    out = []
    ev_titles = " ".join(ev["공시 제목"]) if ev is not None and not ev.empty else ""
    # 문단 앞의 '[섹션 이름]'은 본문이 아니므로 빼고 본다 (예: '매출 및 수주상황'의 '수주')
    text = " ".join(re.sub(r"^\[[^\]]*\]\s*", "", p) for p, _ in paragraphs) + " " + " ".join(headlines)
    if re.search(r"분할|합병|매각|양도|중단영업", ev_titles + text):
        out.append("같은 시기에 **사업 분할·합병·매각** 관련 내용이 있습니다. 실적 변화가 영업 자체보다 "
                   "**사업 구조 변화(중단영업 재분류 등)** 때문일 수 있습니다.")
    if re.search(r"공급계약|수주|판매ㆍ공급", ev_titles + text):
        out.append("같은 시기에 **공급계약·수주** 관련 공시나 기사가 있습니다. 매출 인식 시점과 연결해 보세요.")
    if kind in ("margin", "inv") and re.search(r"손상|평가손실|충당|일회성", text):
        out.append("원문에 **손상·평가손실·충당금** 같은 일회성 성격의 비용 언급이 있습니다.")
    if re.search(r"환율|원화", text):
        out.append("**환율** 언급이 있습니다. 수출 비중이 크면 환율 변화가 매출·이익률에 영향을 줍니다.")
    if re.search(r"수요|가격|판가|출하", text) and kind in ("rev", "margin"):
        out.append("**수요·가격(판가)** 변화 언급이 있습니다. 업황(산업 대시보드의 사이클 신호)과 같은 방향인지 확인하세요.")
    if not out:
        out.append("자동으로 찾은 뚜렷한 단서가 없습니다. 아래 원문 문단과 공시 목록을 직접 확인하세요.")
    return out
