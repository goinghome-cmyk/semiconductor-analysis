"""DART 연결 점검: python scripts/check_dart.py [종목코드]

.streamlit/secrets.toml 의 키를 읽어 재무제표·주식수·감사의견·사업보고서 파싱을 한 번씩 시험합니다.
(키 값은 출력하지 않습니다)
"""
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import disclosure, metrics  # noqa: E402
from src.dart import DartClient  # noqa: E402

secrets = tomllib.loads((ROOT / ".streamlit" / "secrets.toml").read_text(encoding="utf-8"))
key = secrets.get("DART_API_KEY", "")
print("DART 키:", "있음" if key else "없음")
client = DartClient(key)
code = sys.argv[1] if len(sys.argv) > 1 else "000660"
print("고유번호:", client.corp_code(code))

res = metrics.build_company(client, code)
q = res["quarterly"]
print("재무제표:", res["fs_div"], "분기 수", len(q))
print(q[["revenue", "op", "opm", "rev_yoy", "cfo", "cfi", "cff", "equity_owner", "roe"]].tail(6).to_string())
print("주식수:", res["shares"])
print(res["annual"][["revenue", "op", "cfo", "cfi", "cff"]].to_string())

aud = client.audit_opinions(res["corp_code"], q["year"].max() - 1)
print("감사의견 컬럼:", list(aud.columns))
print(aud.head(3).to_string())

from datetime import date, timedelta  # noqa: E402
lst = client.disclosures(res["corp_code"], (date.today() - timedelta(days=500)).strftime("%Y%m%d"),
                         date.today().strftime("%Y%m%d"))
row = lst.sort_values("rcept_dt").iloc[-1]
print("사업보고서:", row["report_nm"], row["rcept_no"])
files = client.document(row["rcept_no"])
print("원문 파일:", list(files))
secs = disclosure.parse_sections(disclosure.main_document(files))
print("대목차:", [s.title for s in secs])
biz = disclosure.find_section(secs, "사업의 내용")
print("사업의 내용 하위:", [c.title for c in biz.children] if biz else None)
note = disclosure.find_section(secs, "연결재무제표 주석") or disclosure.find_section(secs, "재무제표 주석")
if note:
    notes = disclosure.split_notes(disclosure.full_text(note))
    print("주석 수:", len(notes), [t for t, _ in notes][:12])
