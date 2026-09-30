"""현금흐름 패턴(영업/투자/재무 부호)에 따른 기업 상태 분류."""
from __future__ import annotations

import pandas as pd

# (영업, 투자, 재무) 부호 -> (유형, 설명)
CF_TYPES = {
    ("+", "-", "-"): ("우량형", "영업으로 번 현금으로 투자하고, 차입금 상환·배당까지 하는 성숙·우량 기업"),
    ("+", "-", "+"): ("성장형", "영업현금에 외부 자금까지 조달해 적극적으로 투자하는 성장 기업"),
    ("+", "+", "-"): ("구조조정형", "영업현금과 자산 매각 대금으로 부채를 갚는 구조조정 단계"),
    ("-", "-", "+"): ("재활형", "영업은 현금 유출이지만 외부 조달로 투자를 이어가는 회생·초기 단계"),
    ("-", "+", "-"): ("위험형", "영업 현금 유출에 자산을 팔아 부채를 갚는 위험 신호"),
    ("-", "-", "-"): ("도산형", "모든 활동에서 현금이 빠져나가 보유 현금을 소진하는 상태"),
    ("+", "+", "+"): ("현금축적형", "모든 활동에서 현금이 들어와 현금을 쌓는 상태 (대규모 투자·M&A 대비 등)"),
    ("-", "+", "+"): ("쇠퇴형", "영업 현금 유출을 자산 매각과 외부 조달로 메우는 상태"),
}

TYPE_ORDER = ["우량형", "성장형", "현금축적형", "구조조정형", "재활형", "쇠퇴형", "위험형", "도산형"]


def _sign(v) -> str | None:
    if v is None or pd.isna(v):
        return None
    return "+" if v >= 0 else "-"


def classify(cfo, cfi, cff) -> tuple[str, str, str]:
    """-> (유형, 설명, 부호표기). 값이 없으면 ('판단불가', ...)."""
    signs = (_sign(cfo), _sign(cfi), _sign(cff))
    if None in signs:
        return "판단불가", "현금흐름 데이터가 부족합니다.", "?"
    name, desc = CF_TYPES[signs]
    return name, desc, " / ".join(signs)


def reference_table() -> pd.DataFrame:
    rows = [
        {"유형": name, "영업": k[0], "투자": k[1], "재무": k[2], "설명": desc}
        for k, (name, desc) in CF_TYPES.items()
    ]
    df = pd.DataFrame(rows)
    df["_o"] = df["유형"].map(TYPE_ORDER.index)
    return df.sort_values("_o").drop(columns="_o").reset_index(drop=True)
