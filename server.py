"""로스트인지하철 로컬 서버.

실행:  python server.py      → http://127.0.0.1:5600

- public/ 의 화면 파일을 내려준다.
- /api/* 는 SQLite 에 받아 둔 LOST112 습득물에서 찾는다. 키는 서버만 쓴다.
"""
import datetime as dt
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from lost112.client import ApiError, Lost112Client
from lost112.config import ROOT, Settings
from lost112.places import (CATEGORIES, line_meta, line_name, line_sort_key, normalize_station,
                            split_lines)
from lost112.store import Store
from lost112.sync import Syncer

PUBLIC = ROOT / "public"
MIME = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8", ".png": "image/png", ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
}
PERIODS = (3, 7, 14, 30)


def josa(word: str, with_final: str, without_final: str) -> str:
    """받침 유무에 따라 조사 선택: josa('강남', '이', '가') → '이'."""
    last = word[-1:] if word else ""
    if "가" <= last <= "힣":
        return with_final if (ord(last) - 0xAC00) % 28 else without_final
    return f"{with_final}({without_final})"
NEXT_PERIOD = {3: 7, 7: 14, 14: 30}
DETAIL_BATCH = 8


class App:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.db_path)
        self.client = Lost112Client(settings.service_key) if settings.service_key else None
        self.syncer = Syncer(self.client, self.store, settings) if self.client else None
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="detail")
        self._detail_lock = threading.Lock()

    # ---- 공통 -----------------------------------------------------------
    def since(self, days: int) -> str:
        return (dt.date.today() - dt.timedelta(days=days - 1)).isoformat()

    def detail(self, atc_id: str, fd_sn: str) -> dict | None:
        cached = self.store.get_detail(atc_id, fd_sn)
        if cached is not None or not self.client:
            return cached
        data = self.client.detail(atc_id, fd_sn)
        if data:
            self.store.put_detail(atc_id, fd_sn, data)
        return data

    @staticmethod
    def status_of(detail: dict | None) -> dict | None:
        if not detail:
            return None
        text = (detail.get("csteSteNm") or "").strip()
        if not text:
            return None
        # 보관중 → 초록, 반입중·이관 등 옮겨지는 중 → 주황, 수령·종결 등 → 회색
        tone = "keep" if "보관" in text else "moved" if ("이관" in text or "반입" in text or "인계" in text) else "done"
        return {"text": text, "tone": tone}

    def serialize(self, row: dict) -> dict:
        origin = row.get("origin_station")
        here = row.get("station")
        lines = split_lines(row.get("origin_lines") or row.get("lines"))
        return {
            "id": f"{row['atc_id']}:{row['fd_sn']}",
            "name": row.get("name") or "(이름 없음)",
            "cat": row.get("cat"),
            "category": row.get("prdt_cl"),
            "color": (row.get("clr") or "").split("(")[0],
            "ymd": row.get("fd_ymd"),
            "img": row.get("img") or None,
            "place": row.get("dep_place"),
            "kind": row.get("kind"),
            "origin": origin,
            "originGuessed": row.get("origin_src") == "org",
            "movedFrom": origin if row.get("kind") == "center" and origin and origin != here else None,
            "lines": [line_meta(ln) for ln in lines],
        }

    # ---- API -----------------------------------------------------------
    def api_status(self, _q) -> dict:
        return {
            "hasKey": bool(self.client),
            "sync": self.syncer.snapshot() if self.syncer else None,
            "stats": self.store.stats(),
            "maxDays": self.settings.sync_days,
            "categories": CATEGORIES,
        }

    def api_stations(self, q) -> dict:
        text = normalize_station(q.get("q", ""))
        out = []
        for e in self.store.suggest(text):
            lines = sorted(e["lines"], key=line_sort_key)
            out.append({"name": e["name"], "count": e["count"], "lines": [line_meta(ln) for ln in lines]})
        return {"query": text, "stations": out}

    def api_lines(self, _q) -> dict:
        counts = self.store.line_counts(self.since(self.settings.sync_days))
        lines = [ln for ln in counts if line_meta(ln)["widenable"] and counts[ln] >= 3]
        lines.sort(key=line_sort_key)
        return {"lines": [{**line_meta(ln), "count": counts[ln]} for ln in lines]}

    def api_search(self, q) -> dict:
        station = normalize_station(q.get("station", ""))
        line = (q.get("line") or "").strip() or None
        scope = q.get("scope") if q.get("scope") in ("station", "keyword", "line") else "station"
        cat = q.get("cat") if q.get("cat") in CATEGORIES else None
        try:
            days = int(q.get("days", 14))
        except ValueError:
            days = 14
        days = max(1, min(days, self.settings.sync_days))
        notice = None

        station_lines = self.store.station_lines(station) if station else []
        if station and scope == "station" and not station_lines:
            scope = "keyword"
            notice = (f"'{station}'역은 최근 습득물이 없어서, 이름에 '{station}'"
                      f"{josa(station, '이', '가')} 들어간 역을 찾았어요.")
        if scope == "line" and not line:
            scope = "station" if station else "keyword"

        base = dict(station=station or None, line=line, scope=scope, cat=cat)
        total, rows = self.store.search(since=self.since(days), **base)

        widen = []
        if station and scope == "station":
            n = self.store.count(since=self.since(days), **{**base, "scope": "keyword"}) - total
            if n > 0:
                others = self.store.similar_stations(station)
                widen.append({
                    "type": "keyword", "add": n,
                    "title": f"'{station}' 들어간 역 모두",
                    "hint": ", ".join(others[:3]) + (" 등" if len(others) >= 3 else ""),
                    "params": {"scope": "keyword"},
                })
        if station and scope in ("station", "keyword"):
            for ln in station_lines[:2]:
                meta = line_meta(ln)
                if not meta["widenable"]:
                    continue
                n = self.store.count(since=self.since(days), **{**base, "scope": "line", "line": ln}) - total
                if n > 0:
                    widen.append({
                        "type": "line", "add": n, "line": meta,
                        "title": f"{line_name(ln)} 전체",
                        "hint": "열차 안 분실" + (f" · {meta['center']} 포함" if meta["center"] else ""),
                        "params": {"scope": "line", "line": ln},
                    })
        nxt = NEXT_PERIOD.get(days)
        if nxt and nxt <= self.settings.sync_days:
            n = self.store.count(since=self.since(nxt), **base) - total
            if n > 0:
                widen.append({"type": "days", "add": n, "title": f"기간 {nxt}일로 늘리기",
                              "hint": f"지금은 최근 {days}일", "params": {"days": nxt}})

        sync = self.syncer.snapshot() if self.syncer else {}
        return {
            "query": {"station": station or None, "line": line, "scope": scope, "cat": cat, "days": days},
            "station": {"name": station, "lines": [line_meta(ln) for ln in sorted(station_lines, key=line_sort_key)]} if station else None,
            "lineInfo": line_meta(line) if line else None,
            "total": total,
            "items": [self.serialize(r) for r in rows],
            "widen": widen,
            "notice": notice,
            "asOf": sync.get("lastSync") or self.store.get_meta("last_sync"),
            "syncing": bool(sync.get("running")),
        }

    def api_details(self, q) -> dict:
        ids = [i for i in (q.get("ids") or "").split(",") if ":" in i][:DETAIL_BATCH]

        def one(item_id):
            atc, sn = item_id.split(":", 1)
            try:
                d = self.detail(atc, sn)
            except ApiError:
                return item_id, None
            return item_id, {"status": self.status_of(d), "origin": (self.store.get_item(atc, sn) or {}).get("origin_station")}

        results = dict(self._pool.map(one, ids))
        return {"details": results}

    def api_item(self, q) -> dict:
        item_id = q.get("id", "")
        if ":" not in item_id:
            raise ValueError("id 가 필요해요")
        atc, sn = item_id.split(":", 1)
        error = None
        try:
            d = self.detail(atc, sn)
        except ApiError as e:
            d, error = None, str(e)
        row = self.store.get_item(atc, sn)
        if not row and not d:
            return {"item": None, "error": error or "이 습득물을 찾지 못했어요. 이미 수령됐거나 목록에서 내려갔을 수 있어요."}
        item = self.serialize(row) if row else {"id": item_id, "name": d.get("fdPrdtNm"), "lines": []}
        tel = (d or {}).get("tel", "").strip()
        if not tel or set(tel.replace("-", "")) <= {"0"}:
            tel = None
        detail = None
        if d:
            hour = (d.get("fdHor") or "").strip()
            detail = {
                "status": self.status_of(d),
                "foundPlaceType": d.get("fdPlace") or None,
                "hour": f"{int(hour)}시경" if hour.isdigit() else None,
                "keeper": d.get("orgNm") or (row or {}).get("dep_place"),
                "depPlace": d.get("depPlace") or (row or {}).get("dep_place"),
                "tel": tel,
                "note": (d.get("uniq") or "").replace("\r\n", "\n").strip() or None,
                "img": d.get("fdFilePathImg") if d.get("fdFilePathImg") and "no_img" not in d.get("fdFilePathImg") else None,
            }
            refreshed = self.store.get_item(atc, sn)
            if refreshed:
                item = self.serialize(refreshed)
        return {"item": item, "detail": detail, "error": error}


class Handler(BaseHTTPRequestHandler):
    server_version = "LostInSubway/1.0"
    app: App = None  # main() 에서 채운다

    ROUTES = {
        "/api/status": "api_status", "/api/stations": "api_stations", "/api/lines": "api_lines",
        "/api/search": "api_search", "/api/details": "api_details", "/api/item": "api_item",
    }

    def log_message(self, fmt, *args):  # 요청 로그는 오류만
        if args and str(args[1])[:1] in ("4", "5"):
            sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data: dict):
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), MIME[".json"])

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path.startswith("/api/"):
            handler = self.ROUTES.get(url.path)
            if not handler:
                return self._json(404, {"error": "없는 API 예요"})
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                return self._json(200, getattr(self.app, handler)(query))
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            except ApiError as e:
                return self._json(502, {"error": str(e)})
            except Exception as e:  # 화면이 멈추지 않도록 JSON 으로 돌려준다
                sys.stderr.write(f"API 오류 {url.path}: {e!r}\n")
                return self._json(500, {"error": "서버에서 처리하지 못했어요"})
        return self._static(url.path)

    def _static(self, path: str):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        if rel == "favicon.ico":  # 브라우저 기본 요청에도 SVG 아이콘을 준다
            rel = "favicon.svg"
        target = (PUBLIC / rel).resolve()
        if not target.is_relative_to(PUBLIC.resolve()) or not target.is_file():
            return self._send(404, "찾을 수 없어요".encode("utf-8"), "text/plain; charset=utf-8")
        ctype = MIME.get(target.suffix.lower(), "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)


def main():
    sys.stdout.reconfigure(errors="replace")
    settings = Settings.load()
    app = App(settings)
    Handler.app = app
    if app.syncer:
        app.syncer.start()
    else:
        print("[알림] .env 에 LOST112_SERVICE_KEY 가 없어 새 데이터를 받지 않아요. 저장된 데이터로만 검색합니다.")
    server = ThreadingHTTPServer(("127.0.0.1", settings.port), Handler)
    print(f"로스트인지하철 실행 중 → http://127.0.0.1:{settings.port}  (끄려면 Ctrl+C)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
