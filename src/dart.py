"""OpenDART API 클라이언트 (디스크 캐시 포함).

- 과거 보고서는 바뀌지 않으므로 한 번 받은 결과는 계속 재사용합니다.
- '데이터 없음'(아직 공시 전) 응답은 하루만 캐시해서 공시 후 다시 받아옵니다.
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
import time
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

import pandas as pd
import requests

BASE_URL = "https://opendart.fss.or.kr/api"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"

# 보고서 코드
REPRT_Q1, REPRT_H1, REPRT_Q3, REPRT_FY = "11013", "11012", "11014", "11011"
REPRT_ORDER = [REPRT_Q1, REPRT_H1, REPRT_Q3, REPRT_FY]
REPRT_QUARTER = {REPRT_Q1: 1, REPRT_H1: 2, REPRT_Q3: 3, REPRT_FY: 4}

NO_DATA_TTL = 24 * 3600

# ---- 과다 호출 방지 (DART 공식 한도: 키당 하루 20,000건. 짧은 시간 몰아치기도 차단 사유)
MIN_INTERVAL = 1.0        # 요청 사이 최소 간격(초). 분당 최대 60건
DAILY_LIMIT = 3000        # 앱 자체 하루 상한 (공식 한도의 15%)
COOLDOWN = 3600           # 한도 초과·차단 신호를 받으면 1시간 동안 호출 중단
BLOCK_STATUSES = {"020", "021", "800", "901"}  # 요청 제한 초과, 조회 가능 회사 수 초과, 점검, 계정 만료 등

_lock = threading.Lock()
_last_call = 0.0
_blocked_until = 0.0


class DartError(RuntimeError):
    pass


class DartClient:
    def __init__(self, api_key: str, cache_dir: Path = CACHE_DIR, daily_limit: int = DAILY_LIMIT):
        if not api_key:
            raise DartError("DART API 키가 설정되지 않았습니다.")
        self.api_key = api_key
        self.daily_limit = daily_limit
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()

    # ------------------------------------------------------------ 호출 제한
    def _usage_path(self) -> Path:
        return self.cache_dir / f"usage_{time.strftime('%Y%m%d')}.json"

    def usage_today(self) -> int:
        p = self._usage_path()
        return json.loads(p.read_text())["count"] if p.exists() else 0

    def _request(self, url: str, params: dict, timeout: int) -> requests.Response:
        """모든 DART 네트워크 호출은 여기로: 순차·간격 유지·일일 상한·차단 시 즉시 중단."""
        global _last_call, _blocked_until
        with _lock:
            now = time.time()
            if now < _blocked_until:
                mins = int((_blocked_until - now) / 60) + 1
                raise DartError(f"DART 호출을 잠시 멈춘 상태입니다 (약 {mins}분 후 재시도). 캐시된 데이터만 표시됩니다.")
            count = self.usage_today()
            if count >= self.daily_limit:
                raise DartError(f"오늘 새 공시 원문을 받는 한도({self.daily_limit}건)에 도달했습니다. "
                                "이미 받아둔 자료는 그대로 볼 수 있고, 새 자료는 내일 다시 시도해 주세요.")
            wait = MIN_INTERVAL - (now - _last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self.session.get(url, params={"crtfc_key": self.api_key, **params}, timeout=timeout)
            finally:
                _last_call = time.time()
                self._usage_path().write_text(json.dumps({"count": count + 1}))
            if resp.status_code in (403, 429) or resp.status_code >= 500:
                _blocked_until = time.time() + COOLDOWN
                raise DartError(f"DART 서버가 요청을 거부했습니다 (HTTP {resp.status_code}). 1시간 동안 호출을 멈춥니다.")
            resp.raise_for_status()
            return resp

    def _check_status(self, data: dict) -> None:
        global _blocked_until
        status = data.get("status")
        if status in BLOCK_STATUSES:
            _blocked_until = time.time() + COOLDOWN
            raise DartError(f"DART 제한 응답 [{status}] {data.get('message')} — 1시간 동안 호출을 멈춥니다.")
        if status not in ("000", "013"):
            raise DartError(f"DART 오류 [{status}] {data.get('message')}")

    # ------------------------------------------------------------------ 캐시
    def _cache_path(self, name: str, params: dict, ext: str) -> Path:
        key = json.dumps(params, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha1(f"{name}|{key}".encode()).hexdigest()[:16]
        return self.cache_dir / f"{name}_{digest}.{ext}"

    def _get_json(self, endpoint: str, params: dict, ttl: float | None = None) -> dict:
        """ttl=None 이면 정상 응답을 영구 캐시 (과거 보고서는 바뀌지 않음)."""
        path = self._cache_path(endpoint, params, "json")
        if path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            age = time.time() - path.stat().st_mtime
            if cached.get("status") == "000" and (ttl is None or age < ttl):
                return cached
            if cached.get("status") != "000" and age < NO_DATA_TTL:
                return cached

        resp = self._request(f"{BASE_URL}/{endpoint}.json", params, timeout=30)
        data = resp.json()
        self._check_status(data)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    # ------------------------------------------------------------ 고유번호
    def corp_codes(self) -> dict[str, dict]:
        """종목코드 -> {corp_code, corp_name} (상장사만). 30일 캐시."""
        path = self.cache_dir / "corp_codes.json"
        if path.exists() and time.time() - path.stat().st_mtime < 30 * 86400:
            return json.loads(path.read_text(encoding="utf-8"))

        resp = self._request(f"{BASE_URL}/corpCode.xml", {}, timeout=60)
        try:
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
        except zipfile.BadZipFile:
            self._check_status(_error_json(resp.text))
            raise DartError(f"고유번호 파일을 받지 못했습니다: {resp.text[:200]}")
        root = ET.fromstring(zf.read(zf.namelist()[0]))
        mapping = {}
        for item in root.iter("list"):
            stock = (item.findtext("stock_code") or "").strip()
            if stock:
                mapping[stock] = {
                    "corp_code": item.findtext("corp_code"),
                    "corp_name": item.findtext("corp_name"),
                }
        path.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
        return mapping

    def corp_code(self, stock_code: str) -> str:
        info = self.corp_codes().get(stock_code)
        if not info:
            raise DartError(f"종목코드 {stock_code}의 DART 고유번호를 찾을 수 없습니다.")
        return info["corp_code"]

    # ------------------------------------------------------------ 재무제표
    def financials(self, corp_code: str, year: int, reprt_code: str,
                   prefer: str | None = None) -> tuple[pd.DataFrame, str]:
        """단일회사 전체 재무제표. 연결(CFS) 우선, 없으면 별도(OFS). prefer 가 있으면 그것만."""
        for fs_div in ((prefer,) if prefer else ("CFS", "OFS")):
            data = self._get_json(
                "fnlttSinglAcntAll",
                {"corp_code": corp_code, "bsns_year": str(year),
                 "reprt_code": reprt_code, "fs_div": fs_div},
            )
            if data.get("status") == "000" and data.get("list"):
                return pd.DataFrame(data["list"]), fs_div
        return pd.DataFrame(), ""

    def shares(self, corp_code: str, year: int, reprt_code: str) -> dict | None:
        """주식의 총수 현황. 유통주식수(발행주식 - 자기주식)를 돌려줍니다."""
        data = self._get_json(
            "stockTotqySttus",
            {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": reprt_code},
        )
        if data.get("status") != "000":
            return None
        rows = {r.get("se", "").replace(" ", ""): r for r in data.get("list", [])}
        row = rows.get("합계") or rows.get("보통주")
        if not row:
            return None
        common = rows.get("보통주") or row
        return {
            "issued": to_number(row.get("istc_totqy")),
            "treasury": to_number(row.get("tesstk_co")),
            "outstanding": to_number(row.get("distb_stock_co")),
            "common_outstanding": to_number(common.get("distb_stock_co")),
        }

    def audit_opinions(self, corp_code: str, year: int) -> pd.DataFrame:
        """회계감사인의 명칭 및 감사의견 (사업보고서 기준)."""
        data = self._get_json(
            "accnutAdtorNmNdAdtOpinion",
            {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": REPRT_FY},
        )
        if data.get("status") != "000":
            return pd.DataFrame()
        return pd.DataFrame(data.get("list", []))

    # ------------------------------------------------------------ 공시/원문
    def disclosures(self, corp_code: str, bgn_de: str, end_de: str,
                    detail_ty: str = "A001") -> pd.DataFrame:
        """공시 목록. A001=사업보고서, A002=반기, A003=분기."""
        data = self._get_json(
            "list",
            {"corp_code": corp_code, "bgn_de": bgn_de, "end_de": end_de,
             "pblntf_detail_ty": detail_ty, "last_reprt_at": "Y", "page_count": "100"},
            ttl=NO_DATA_TTL,
        )
        if data.get("status") != "000":
            return pd.DataFrame()
        return pd.DataFrame(data.get("list", []))

    def document(self, rcept_no: str) -> dict[str, str]:
        """공시서류 원본(zip) -> {파일명: 텍스트(XML)}. 영구 캐시."""
        path = self.cache_dir / f"doc_{rcept_no}.zip"
        if not path.exists():
            resp = self._request(f"{BASE_URL}/document.xml", {"rcept_no": rcept_no}, timeout=120)
            if not resp.content.startswith(b"PK"):
                self._check_status(_error_json(resp.text))
                raise DartError(f"공시 원문을 받지 못했습니다: {resp.text[:200]}")
            path.write_bytes(resp.content)

        files = {}
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                raw = zf.read(name)
                for enc in ("utf-8", "cp949"):
                    try:
                        files[name] = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
        return files


def _error_json(text: str) -> dict:
    """zip 대신 온 오류 응답(XML/JSON)에서 status/message 추출."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        root = ET.fromstring(text)
        return {"status": root.findtext("status"), "message": root.findtext("message")}
    except ET.ParseError:
        return {}


def to_number(value) -> float | None:
    if value is None or (isinstance(value, float) and value != value):  # None / NaN
        return None
    s = str(value).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None
