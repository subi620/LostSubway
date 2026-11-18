"""로컬 서버(/api/*) 동작 확인. 서버가 켜져 있어야 한다:  python tools/smoke_api.py"""
import json
import sys
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = "http://127.0.0.1:5600/api/"


def get(path, **params):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def show_search(label, **params):
    d = get("search", **params)
    q = d["query"]
    print(f"\n### {label} → {d['total']}건 (scope={q['scope']}, days={q['days']})")
    if d["notice"]:
        print("   notice:", d["notice"])
    if d["station"]:
        print("   역 노선:", [l["name"] for l in d["station"]["lines"]])
    for it in d["items"][:4]:
        print(f"   {it['ymd']} {it['name'][:18]:18s} | {it['place']} | 원래역 {it['movedFrom']}")
    for w in d["widen"]:
        print(f"   [넓히기] {w['title']} ({w['hint']}) +{w['add']} {w['params']}")
    return d


st = get("status")
print("status:", st["stats"], "| sync:", {k: st["sync"][k] for k in ("running", "phase", "done", "total", "error")})
print("\nstations '강남':", [(s["name"], [l["label"] for l in s["lines"]], s["count"]) for s in get("stations", q="강남")["stations"]])
print("stations '홍대':", [(s["name"], [l["label"] for l in s["lines"]]) for s in get("stations", q="홍대")["stations"]])
print("lines:", [(l["name"], l["count"], l["center"]) for l in get("lines")["lines"]])

d = show_search("강남역 · 지갑·카드 · 14일", station="강남역", cat="wallet", days=14)
show_search("강남 · 전체 · 14일", station="강남", days=14)
show_search("강남 + 2호선 전체 · 지갑·카드 · 14일", station="강남", cat="wallet", days=14, scope="line", line="2호선")
show_search("2호선 열차 안 · 휴대폰 · 7일", scope="line", line="2호선", cat="phone", days=7)
show_search("없는 이름 '강남구'", station="강남구", days=30)

ids = [it["id"] for it in d["items"][:3]] or [it["id"] for it in get("search", station="강남", days=30)["items"][:3]]
print("\ndetails:", get("details", ids=",".join(ids))["details"])
item = get("item", id=ids[0])
print("item:", item["item"]["name"], "|", {k: item["detail"][k] for k in ("status", "hour", "tel", "foundPlaceType", "depPlace")} if item["detail"] else item["error"])
