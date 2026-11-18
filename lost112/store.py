"""SQLite 저장소.

items   : 습득물 목록 (보관장소 해석 결과와 '원래 습득역' 포함)
orgs    : 기관번호(atcId 앞 8자리) → 역 매핑. 역 보관 물건에서 배운다.
details : 상세조회 캐시
meta    : 동기화 시각, 스키마 버전
"""
import datetime as dt
import json
import sqlite3
import threading
from collections import defaultdict
from pathlib import Path

from .places import classify, join_lines, org_of, parse_place, parse_uniq_origin, split_lines

SCHEMA_VERSION = "2"
SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    atc_id         TEXT NOT NULL,
    fd_sn          TEXT NOT NULL,
    fd_ymd         TEXT NOT NULL,       -- 'YYYY-MM-DD' 습득일
    prdt_cl        TEXT,                -- '휴대폰 > 아이폰'
    cat            TEXT,                -- wallet/phone/bag/umbrella/device/etc
    clr            TEXT,
    name           TEXT,
    img            TEXT,                -- 실제 사진이 있을 때만
    dep_place      TEXT,                -- 보관장소 원문
    kind           TEXT,                -- station/center/other
    station        TEXT,                -- 지금 보관 중인 역/센터 이름
    lines          TEXT,                -- 지금 보관 장소의 노선 ',2호선,'
    is_transit     INTEGER NOT NULL DEFAULT 0,
    origin_station TEXT,                -- 원래 습득한 역 (모르면 NULL)
    origin_lines   TEXT,
    origin_src     TEXT,                -- place(역 보관) / org(기관번호) / detail(상세 특이사항)
    updated_at     TEXT,
    PRIMARY KEY (atc_id, fd_sn)
);
CREATE INDEX IF NOT EXISTS ix_items_ymd ON items (fd_ymd);
CREATE INDEX IF NOT EXISTS ix_items_origin ON items (origin_station);
CREATE TABLE IF NOT EXISTS orgs (
    org        TEXT PRIMARY KEY,
    station    TEXT NOT NULL,
    lines      TEXT,
    dep_place  TEXT,
    n          INTEGER,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS details (
    atc_id     TEXT NOT NULL,
    fd_sn      TEXT NOT NULL,
    data       TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (atc_id, fd_sn)
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

COLUMNS = ("atc_id", "fd_sn", "fd_ymd", "prdt_cl", "cat", "clr", "name", "img", "dep_place",
           "kind", "station", "lines", "is_transit", "origin_station", "origin_lines",
           "origin_src", "updated_at")
_BASE_UPDATE = ", ".join(f"{c} = excluded.{c}" for c in COLUMNS[2:13] + ("updated_at",))
UPSERT = (
    f"INSERT INTO items ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))}) "
    f"ON CONFLICT (atc_id, fd_sn) DO UPDATE SET {_BASE_UPDATE}, "
    # 역에 있다가 센터로 옮겨진 물건은 예전에 알던 원래 역을 그대로 둔다
    "origin_station = CASE WHEN excluded.origin_src IS NOT NULL THEN excluded.origin_station ELSE items.origin_station END, "
    "origin_lines = CASE WHEN excluded.origin_src IS NOT NULL THEN excluded.origin_lines ELSE items.origin_lines END, "
    "origin_src = COALESCE(excluded.origin_src, items.origin_src)"
)
ORG_MIN_SHARE = 0.8  # 한 기관번호의 역 보관 물건 중 이 비율 이상이 한 역이면 그 역으로 본다


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def norm_ymd(value: str) -> str:
    digits = "".join(ch for ch in value or "" if ch.isdigit())
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) >= 8 else ""


def real_image(url: str) -> str:
    return "" if not url or "no_img" in url else url


def to_row(item: dict, stamp: str) -> tuple | None:
    atc = item.get("atcId", "").strip()
    ymd = norm_ymd(item.get("fdYmd", ""))
    if not atc or not ymd:
        return None
    kind, station, lines, transit = parse_place(item.get("depPlace", ""))
    prdt = item.get("prdtClNm", "")
    name = item.get("fdPrdtNm", "")
    at_station = kind == "station" and transit
    return (
        atc, item.get("fdSn", "1") or "1", ymd, prdt, classify(prdt, name),
        item.get("clrNm", ""), name, real_image(item.get("fdFilePathImg", "")),
        item.get("depPlace", ""), kind, station, join_lines(lines), int(transit),
        station if at_station else None, join_lines(lines) if at_station else None,
        "place" if at_station else None, stamp,
    )


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
            if self.get_meta("schema") != SCHEMA_VERSION:
                # 해석 규칙이 바뀌면 다시 받으면 되는 데이터라 새로 만든다
                with self._db:
                    self._db.executescript(
                        "DROP TABLE IF EXISTS items; DROP TABLE IF EXISTS orgs; "
                        "DELETE FROM meta WHERE k IN ('last_sync', 'last_full');")
            self._db.executescript(SCHEMA)
            self.set_meta("schema", SCHEMA_VERSION)

    # ---- 메타 -----------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (key, value))

    # ---- 동기화 ---------------------------------------------------------
    def replace_day(self, ymd: str, items: list[dict]) -> tuple[int, int]:
        """하루치를 API 결과와 같게 맞춘다. (저장 건수, 삭제 건수)"""
        stamp = now_iso()
        rows = [r for r in (to_row(it, stamp) for it in items) if r]
        fresh = {(r[0], r[1]) for r in rows if r[2] == ymd}
        with self._lock, self._db:
            existing = self._db.execute(
                "SELECT atc_id, fd_sn FROM items WHERE fd_ymd = ?", (ymd,)).fetchall()
            stale = [(a, s) for a, s in existing if (a, s) not in fresh]
            self._db.executemany("DELETE FROM items WHERE atc_id = ? AND fd_sn = ?", stale)
            self._db.executemany(UPSERT, rows)
        return len(rows), len(stale)

    def rebuild_origins(self) -> dict:
        """역 보관 물건으로 '기관번호 → 역'을 배우고, 센터 보관 물건에 원래 역을 채운다."""
        with self._lock, self._db:
            rows = self._db.execute(
                "SELECT substr(atc_id, 1, 8), dep_place, station, lines, COUNT(*) FROM items "
                "WHERE kind = 'station' AND is_transit = 1 GROUP BY 1, 2").fetchall()
            by_org: dict[str, list] = defaultdict(list)
            for org, dep, station, lines, n in rows:
                by_org[org].append((n, dep, station, lines))
            stamp = now_iso()
            learned = dropped = 0
            for org, entries in by_org.items():
                total = sum(e[0] for e in entries)
                n, dep, station, lines = max(entries)
                if n / total >= ORG_MIN_SHARE:
                    self._db.execute(
                        "INSERT OR REPLACE INTO orgs (org, station, lines, dep_place, n, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)", (org, station, lines, dep, total, stamp))
                    learned += 1
                elif total >= 5:  # 여러 역에 걸친 기관번호는 쓰지 않는다
                    self._db.execute("DELETE FROM orgs WHERE org = ?", (org,))
                    dropped += 1
            cur = self._db.execute(
                "UPDATE items SET "
                "origin_station = (SELECT station FROM orgs WHERE orgs.org = substr(items.atc_id, 1, 8)), "
                "origin_lines = (SELECT lines FROM orgs WHERE orgs.org = substr(items.atc_id, 1, 8)), "
                "origin_src = 'org' "
                "WHERE kind = 'center' AND (origin_src IS NULL OR origin_src = 'org') "
                "AND substr(atc_id, 1, 8) IN (SELECT org FROM orgs)")
        return {"orgs": learned, "ambiguous": dropped, "linked": cur.rowcount}

    def purge_before(self, ymd: str) -> int:
        with self._lock, self._db:
            cur = self._db.execute("DELETE FROM items WHERE fd_ymd < ?", (ymd,))
            week_ago = (dt.datetime.now() - dt.timedelta(days=7)).isoformat(timespec="seconds")
            self._db.execute("DELETE FROM details WHERE fetched_at < ?", (week_ago,))
        return cur.rowcount

    def stats(self) -> dict:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*), SUM(is_transit), MIN(fd_ymd), MAX(fd_ymd), "
                "SUM(kind = 'center'), SUM(kind = 'center' AND origin_station IS NOT NULL) FROM items"
            ).fetchone()
        return {"items": row[0] or 0, "transit": row[1] or 0, "from": row[2], "to": row[3],
                "atCenters": row[4] or 0, "centerWithOrigin": row[5] or 0}

    # ---- 검색 -----------------------------------------------------------
    @staticmethod
    def _where(*, station=None, line=None, scope="station", cat=None, since=None):
        where = ["is_transit = 1"]
        args: list = []
        if since:
            where.append("fd_ymd >= ?")
            args.append(since)
        line_like = f"%,{line},%" if line else None
        # 노선 조건: 원래 역의 노선이 맞거나, 원래 역을 모르는 센터 물건 중 그 노선 담당 센터
        line_cond = "(origin_lines LIKE ? OR (origin_station IS NULL AND lines LIKE ?))"
        if scope == "keyword" and station:
            where.append("origin_station LIKE ? ESCAPE '\\'")
            args.append(_like(station))
        elif scope == "line" and line:
            if station:
                where.append(f"(origin_station = ? OR {line_cond})")
                args += [station, line_like, line_like]
            else:
                where.append(line_cond)
                args += [line_like, line_like]
        elif station:
            where.append("origin_station = ?")
            args.append(station)
        elif line:
            where.append(line_cond)
            args += [line_like, line_like]
        if cat:
            where.append("cat = ?")
            args.append(cat)
        return " AND ".join(where), args

    def count(self, **query) -> int:
        where, args = self._where(**query)
        with self._lock:
            return self._db.execute(f"SELECT COUNT(*) FROM items WHERE {where}", args).fetchone()[0]

    def search(self, limit: int = 300, **query) -> tuple[int, list[dict]]:
        where, args = self._where(**query)
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) FROM items WHERE {where}", args).fetchone()[0]
            rows = self._db.execute(
                f"SELECT * FROM items WHERE {where} ORDER BY fd_ymd DESC, atc_id DESC LIMIT ?",
                args + [limit]).fetchall()
        return total, [dict(r) for r in rows]

    def station_lines(self, station: str) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT origin_lines, COUNT(*) n FROM items WHERE is_transit = 1 AND origin_station = ? "
                "GROUP BY 1 ORDER BY n DESC", (station,)).fetchall()
        out: list[str] = []
        for lines, _ in rows:
            out += [ln for ln in split_lines(lines) if ln not in out]
        return out

    def similar_stations(self, station: str, limit: int = 3) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT origin_station, COUNT(*) n FROM items WHERE is_transit = 1 "
                "AND origin_station LIKE ? ESCAPE '\\' AND origin_station != ? "
                "GROUP BY 1 ORDER BY n DESC LIMIT ?", (_like(station), station, limit)).fetchall()
        return [r[0] for r in rows]

    def suggest(self, text: str, limit: int = 10) -> list[dict]:
        if not text:
            return []
        with self._lock:
            rows = self._db.execute(
                "SELECT origin_station, origin_lines, COUNT(*) FROM items WHERE is_transit = 1 "
                "AND origin_station LIKE ? ESCAPE '\\' GROUP BY 1, 2", (_like(text),)).fetchall()
        merged: dict[str, dict] = {}
        for station, lines, n in rows:
            entry = merged.setdefault(station, {"name": station, "lines": [], "count": 0})
            entry["count"] += n
            entry["lines"] += [ln for ln in split_lines(lines) if ln not in entry["lines"]]
        ranked = sorted(merged.values(), key=lambda e: (
            0 if e["name"] == text else 1 if e["name"].startswith(text) else 2, -e["count"], e["name"]))
        return ranked[:limit]

    def line_counts(self, since: str) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT COALESCE(origin_lines, lines), COUNT(*) FROM items "
                "WHERE is_transit = 1 AND fd_ymd >= ? GROUP BY 1", (since,)).fetchall()
        counts: dict[str, int] = {}
        for lines, n in rows:
            for ln in split_lines(lines):
                counts[ln] = counts.get(ln, 0) + n
        return counts

    def get_item(self, atc_id: str, fd_sn: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM items WHERE atc_id = ? AND fd_sn = ?", (atc_id, fd_sn)).fetchone()
        return dict(row) if row else None

    # ---- 상세 캐시 ------------------------------------------------------
    def get_detail(self, atc_id: str, fd_sn: str, max_age_hours: float = 6) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT data, fetched_at FROM details WHERE atc_id = ? AND fd_sn = ?",
                (atc_id, fd_sn)).fetchone()
        if not row:
            return None
        age = dt.datetime.now() - dt.datetime.fromisoformat(row[1])
        return json.loads(row[0]) if age.total_seconds() < max_age_hours * 3600 else None

    def put_detail(self, atc_id: str, fd_sn: str, data: dict) -> None:
        """상세를 저장하고, 특이사항에 원래 습득역이 있으면 그 물건의 원래 역을 고친다."""
        origin = parse_uniq_origin(data.get("uniq", ""))
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO details (atc_id, fd_sn, data, fetched_at) VALUES (?, ?, ?, ?)",
                (atc_id, fd_sn, json.dumps(data, ensure_ascii=False), now_iso()))
            if origin:
                self._db.execute(
                    "UPDATE items SET origin_station = ?, origin_lines = ?, origin_src = 'detail' "
                    "WHERE atc_id = ? AND fd_sn = ? AND kind = 'center'",
                    (origin[0], join_lines(origin[1]), atc_id, fd_sn))

    def org_station(self, atc_id: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT station FROM orgs WHERE org = ?", (org_of(atc_id),)).fetchone()
        return row[0] if row else None
