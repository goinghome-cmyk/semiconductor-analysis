"""한국은행 ECOS Open API 클라이언트 (DART 와 같은 방식: 순차·간격·캐시·차단 시 중단).

URL 형식: https://ecos.bok.or.kr/api/{서비스}/{키}/json/kr/{시작}/{끝}/{인자...}
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://ecos.bok.or.kr/api"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "ecos"

MIN_INTERVAL = 1.0
DAILY_LIMIT = 1000
COOLDOWN = 3600
DATA_TTL = 24 * 3600          # 월별 통계는 하루 한 번만 새로 확인
META_TTL = 30 * 24 * 3600     # 통계표·항목 목록은 한 달

_lock = threading.Lock()
_last_call = 0.0
_blocked_until = 0.0


class EcosError(RuntimeError):
    pass


class EcosClient:
    def __init__(self, api_key: str, cache_dir: Path = CACHE_DIR):
        if not api_key:
            raise EcosError("ECOS API 키가 설정되지 않았습니다.")
        self.api_key = api_key
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()

    def _usage_path(self) -> Path:
        return self.cache_dir.parent / f"usage_ecos_{time.strftime('%Y%m%d')}.json"

    def usage_today(self) -> int:
        p = self._usage_path()
        return json.loads(p.read_text())["count"] if p.exists() else 0

    def _call(self, service: str, args: list[str], ttl: float) -> dict:
        global _last_call, _blocked_until
        key = json.dumps([service, args], ensure_ascii=False)
        path = self.cache_dir / f"{service}_{hashlib.sha1(key.encode()).hexdigest()[:16]}.json"
        if path.exists() and time.time() - path.stat().st_mtime < ttl:
            return json.loads(path.read_text(encoding="utf-8"))

        with _lock:
            now = time.time()
            if now < _blocked_until:
                raise EcosError("ECOS 호출을 잠시 멈춘 상태입니다. 캐시된 데이터만 표시됩니다.")
            count = self.usage_today()
            if count >= DAILY_LIMIT:
                raise EcosError(f"오늘 ECOS 호출이 앱 상한({DAILY_LIMIT}건)에 도달했습니다.")
            wait = MIN_INTERVAL - (now - _last_call)
            if wait > 0:
                time.sleep(wait)
            url = "/".join([BASE_URL, service, self.api_key, "json", "kr", *args])
            try:
                resp = self.session.get(url, timeout=30)
            finally:
                _last_call = time.time()
                self._usage_path().write_text(json.dumps({"count": count + 1}))
            if resp.status_code in (403, 429) or resp.status_code >= 500:
                _blocked_until = time.time() + COOLDOWN
                raise EcosError(f"ECOS 서버가 요청을 거부했습니다 (HTTP {resp.status_code}). 1시간 동안 호출을 멈춥니다.")
            resp.raise_for_status()
            data = resp.json()

        result = data.get("RESULT")
        if result:
            code = result.get("CODE", "")
            if code == "INFO-200":  # 데이터 없음
                data = {service: {"row": []}}
            elif code.startswith("ERROR-6") or code in ("ERROR-100",):
                # 6xx: 호출 한도·과다 호출, 100: 인증키 오류
                _blocked_until = time.time() + COOLDOWN
                raise EcosError(f"ECOS 제한/인증 오류 [{code}] {result.get('MESSAGE')}")
            else:
                raise EcosError(f"ECOS 오류 [{code}] {result.get('MESSAGE')}")
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    # ------------------------------------------------------------------ API
    def tables(self) -> pd.DataFrame:
        data = self._call("StatisticTableList", ["1", "10000"], META_TTL)
        return pd.DataFrame(data.get("StatisticTableList", {}).get("row", []))

    def items(self, stat_code: str) -> pd.DataFrame:
        data = self._call("StatisticItemList", ["1", "2000", stat_code], META_TTL)
        return pd.DataFrame(data.get("StatisticItemList", {}).get("row", []))

    def series(self, stat_code: str, cycle: str, start: str, end: str,
               *item_codes: str) -> pd.Series:
        """시계열 하나 -> pd.Series (index: 기간 문자열 'YYYYMM' 등)."""
        data = self._call("StatisticSearch",
                          ["1", "10000", stat_code, cycle, start, end, *item_codes], DATA_TTL)
        rows = data.get("StatisticSearch", {}).get("row", [])
        if not rows:
            return pd.Series(dtype=float)
        df = pd.DataFrame(rows)
        return pd.Series(pd.to_numeric(df["DATA_VALUE"], errors="coerce").values,
                         index=df["TIME"].values, name=rows[0].get("ITEM_NAME1"))
