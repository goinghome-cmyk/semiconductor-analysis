"""데이터 갱신·예열 스크립트:  python scripts/refresh_data.py

방문자가 페이지를 열 때 DART 를 새로 부르지 않도록, 앱이 쓰는 자료를 미리 캐시(data/cache)에 받아 둔다.
- 로컬: .streamlit/secrets.toml 의 키를 읽는다
- GitHub Actions: 환경변수 DART_API_KEY, ECOS_API_KEY 를 읽는다
DART 호출은 DartClient 가 1초 간격·일일 상한·차단 시 중단을 그대로 지킨다.
"""
import logging
import os
import sys
import tomllib
import warnings
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
logging.disable(logging.WARNING)       # Streamlit 캐시를 앱 밖에서 쓸 때 나오는 경고 숨김
warnings.filterwarnings("ignore")

secrets_file = ROOT / ".streamlit" / "secrets.toml"
if secrets_file.exists():
    for k, v in tomllib.loads(secrets_file.read_text(encoding="utf-8")).items():
        if v and k not in os.environ:
            os.environ[k] = str(v)

from src import anomaly, appdata, industry, metrics  # noqa: E402
from src.dart import DartError  # noqa: E402
from src.ecos import EcosClient, EcosError  # noqa: E402


def step(label, fn):
    try:
        fn()
        print(f"  ✓ {label}")
    except (DartError, EcosError) as e:
        print(f"  ✗ {label}: {e}")
        raise SystemExit(1)  # 한도·차단이면 바로 멈춘다
    except Exception as e:  # 한 항목 실패가 전체를 막지 않도록
        print(f"  ! {label}: {type(e).__name__}: {e}")


def main():
    client = appdata.dart()
    before = client.usage_today()
    for name, cfg in appdata.industries().items():
        print(f"[{name}]")
        for c in cfg["companies"]:
            code = c["stock_code"]
            kw = (c.get("business_segment") or {}).get("keyword")
            print(f"- {c['name']}")
            d = {}
            step("재무제표", lambda: d.update(metrics.build_company(client, code, segment_keyword=kw)))
            if not d or d["quarterly"].empty:
                continue
            last = d["quarterly"].index[-1]
            step("사업보고서 원문·주석", lambda: appdata.note_blocks(code))
            step("주석 숫자(충당금)", lambda: appdata.note_numbers(code))
            step("감사의견", lambda: appdata.audit(d["corp_code"]))
            step("가동률", lambda: appdata.utilization_info(code, last))
            model = appdata.model_of(cfg, c["name"])
            if model and model.get("backlog"):
                step("수주잔고", lambda: appdata.order_info(code, last))
            recent = set(d["quarterly"].index[-2:])
            for a in anomaly.detect(d["quarterly"]):
                if a.period not in recent:
                    continue
                start, end, rp = anomaly.period_dates(a.period)
                step(f"이상치 {a.period} 보고서", lambda: appdata.period_report_blocks(code, rp))
                step(f"이상치 {a.period} 공시", lambda: appdata.disclosures_between(
                    code, (start - timedelta(days=30)).strftime("%Y%m%d"),
                    (end + timedelta(days=75)).strftime("%Y%m%d")))
        if appdata.secret("ECOS_API_KEY") and cfg.get("ecos"):
            step("한국은행 지표", lambda: industry.ecos_indicators(EcosClient(appdata.secret("ECOS_API_KEY")), cfg))
    print(f"DART 호출: {client.usage_today() - before}건 (오늘 누적 {client.usage_today()}건)")


if __name__ == "__main__":
    main()
