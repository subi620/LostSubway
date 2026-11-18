"""LOST112(경찰청) 공공데이터 API 키와 실제 응답 형태를 확인하는 스크립트.

- 키 값은 화면에 출력하지 않습니다.
- 실행:  python tools/probe_api.py [검색어]
"""
import datetime as dt
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://apis.data.go.kr/1320000"


def load_key() -> str:
    key = os.environ.get("LOST112_SERVICE_KEY", "").strip()
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line.startswith("LOST112_SERVICE_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if "%" in key:  # Encoding 키를 넣은 경우
        key = urllib.parse.unquote(key)
    return key


def call(service: str, op: str, params: dict, key: str):
    query = urllib.parse.urlencode({"serviceKey": key, **params})
    url = f"{BASE}/{service}/{op}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "lost-in-subway/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def summarize(label: str, status: int, ctype: str, body: bytes, show_items: int = 3):
    print(f"\n=== {label} ===")
    print(f"HTTP {status} | {ctype}")
    text = body.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        print("XML 아님. 앞부분:", text[:400].replace("\n", " "))
        return []
    def find(tag):
        el = root.find(f".//{tag}")
        return el.text.strip() if el is not None and el.text else None
    for tag in ("resultCode", "resultMsg", "resultMag", "errMsg", "returnAuthMsg", "returnReasonCode", "totalCount"):
        v = find(tag)
        if v is not None:
            print(f"{tag}: {v}")
    items = [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]
    print(f"items: {len(items)}")
    if items:
        print("fields:", ", ".join(items[0].keys()))
        for it in items[:show_items]:
            print("  -", {k: (v[:60] + "…" if len(v) > 60 else v) for k, v in it.items()})
    elif not any(find(t) for t in ("resultCode", "errMsg")):
        print("앞부분:", text[:400].replace("\n", " "))
    return items


def main():
    key = load_key()
    if not key:
        print(".env 의 LOST112_SERVICE_KEY 가 비어 있습니다. 키를 넣고 다시 실행하세요.")
        sys.exit(1)
    print(f"키 확인: 길이 {len(key)}자 (값은 출력하지 않음)")
    word = sys.argv[1] if len(sys.argv) > 1 else "강남"
    today = dt.date.today()
    start = (today - dt.timedelta(days=7)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    # 1) 포털기관(지하철·공항 등) 습득물 — 보관장소/물품명 검색
    s, c, b = call("LosPtfundInfoInqireService", "getPtLosfundInfoAccTpNmCstdyPlace",
                   {"DEP_PLACE": word, "pageNo": 1, "numOfRows": 10}, key)
    pt_items = summarize(f"포털기관 · 보관장소='{word}'", s, c, b)

    # 2) 포털기관 습득물 — 기간 검색 (최근 7일)
    s, c, b = call("LosPtfundInfoInqireService", "getPtLosfundInfoAccToClAreaPd",
                   {"START_YMD": start, "END_YMD": end, "pageNo": 1, "numOfRows": 10}, key)
    pd_items = summarize(f"포털기관 · 기간 {start}~{end}", s, c, b)

    # 3) 포털기관 습득물 상세 (위 결과 첫 건)
    first = (pt_items or pd_items or [None])[0]
    if first and first.get("atcId"):
        s, c, b = call("LosPtfundInfoInqireService", "getPtLosfundDetailInfo",
                       {"ATC_ID": first["atcId"], "FD_SN": first.get("fdSn", "1")}, key)
        summarize("포털기관 · 상세", s, c, b, show_items=1)

    # 4) 경찰서 보관 습득물 — 물품명/보관장소 검색
    s, c, b = call("LosfundInfoInqireService", "getLosfundInfoAccTpNmCstdyPlace",
                   {"PRDT_NM": "지갑", "pageNo": 1, "numOfRows": 5}, key)
    summarize("경찰서 습득물 · 물품명='지갑'", s, c, b)

    # 5) JSON 지원 여부
    s, c, b = call("LosPtfundInfoInqireService", "getPtLosfundInfoAccTpNmCstdyPlace",
                   {"DEP_PLACE": word, "pageNo": 1, "numOfRows": 1, "_type": "json"}, key)
    print(f"\n=== _type=json 시도 ===\nHTTP {s} | {c}\n앞부분: {b[:200].decode('utf-8', 'replace')}")


if __name__ == "__main__":
    main()
