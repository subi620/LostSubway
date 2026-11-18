"""백그라운드 동기화.

- 처음(또는 하루에 한 번): 최근 SYNC_DAYS 일치를 하루 단위로 전부 받는다 (약 30회 호출).
- 그 사이에는 REFRESH_MINUTES 마다 최근 REFRESH_DAYS 일치만 다시 받는다.
- 하루치를 다 받으면 DB 의 그날 데이터를 API 결과와 똑같이 맞춘다 (사라진 건 삭제).

단독 실행:  python -m lost112.sync [일수]   → 한 번만 받고 통계 출력
"""
import datetime as dt
import sys
import threading
import time

from .client import PAGE_SIZE, ApiError, Lost112Client
from .config import Settings
from .store import Store, now_iso

FULL_SYNC_EVERY = dt.timedelta(hours=20)


class Syncer(threading.Thread):
    def __init__(self, client: Lost112Client, store: Store, settings: Settings):
        super().__init__(name="lost112-sync", daemon=True)
        self.client = client
        self.store = store
        self.settings = settings
        self._wake = threading.Event()
        self.state = {
            "running": False, "phase": "idle", "done": 0, "total": 0, "error": None,
            "lastSync": store.get_meta("last_sync"), "lastFull": store.get_meta("last_full"),
        }

    def snapshot(self) -> dict:
        state = dict(self.state)
        state["lastSync"] = self.store.get_meta("last_sync")
        state["lastFull"] = self.store.get_meta("last_full")
        return state

    def trigger(self) -> None:
        self._wake.set()

    def _needs_full(self) -> bool:
        last = self.store.get_meta("last_full")
        return not last or dt.datetime.now() - dt.datetime.fromisoformat(last) > FULL_SYNC_EVERY

    def run(self) -> None:
        while True:
            full = self._needs_full()
            try:
                self.sync(self.settings.sync_days if full else self.settings.refresh_days, full)
            except Exception as e:  # 스레드가 죽지 않도록
                self.state.update(running=False, phase="idle", error=f"동기화 오류: {e}")
            self._wake.wait(self.settings.refresh_minutes * 60)
            self._wake.clear()

    def _fetch_day(self, day: dt.date) -> list[dict]:
        ymd = day.strftime("%Y%m%d")
        items: list[dict] = []
        page = 1
        while True:
            total, chunk = self.client.list_day(ymd, page, PAGE_SIZE)
            items += chunk
            if not chunk or len(items) >= total:
                return items
            page += 1

    def sync(self, ndays: int, full: bool, log=None) -> None:
        today = dt.date.today()
        days = [today - dt.timedelta(days=i) for i in range(ndays)]  # 최신 날짜부터
        self.state.update(running=True, phase="full" if full else "refresh",
                          done=0, total=len(days), error=None)
        failed = 0
        for day in days:
            try:
                items = self._fetch_day(day)
            except ApiError as e:
                self.state["error"] = str(e)
                if e.fatal:
                    break
                failed += 1
                continue
            saved, removed = self.store.replace_day(day.isoformat(), items)
            self.state["done"] += 1
            if log:
                log(f"  {day}  {saved:5d}건 저장, {removed:4d}건 삭제")
            time.sleep(0.2)  # API 에 연달아 몰리지 않게
        keep_from = (today - dt.timedelta(days=self.settings.sync_days - 1)).isoformat()
        self.store.purge_before(keep_from)
        linked = self.store.rebuild_origins()
        if log:
            log(f"  원래 습득역 연결: {linked}")
        if self.state["done"]:
            stamp = now_iso()
            self.store.set_meta("last_sync", stamp)
            self.state["lastSync"] = stamp
            if full and self.state["done"] == len(days):
                self.store.set_meta("last_full", stamp)
                self.state["lastFull"] = stamp
        if failed and not self.state["error"]:
            self.state["error"] = f"{failed}일치를 받지 못했어요"
        self.state.update(running=False, phase="idle")


def main() -> None:
    sys.stdout.reconfigure(errors="replace")
    settings = Settings.load()
    if not settings.service_key:
        print(".env 의 LOST112_SERVICE_KEY 가 비어 있습니다.")
        sys.exit(1)
    store = Store(settings.db_path, settings.turso_url, settings.turso_token)
    syncer = Syncer(Lost112Client(settings.service_key), store, settings)
    if len(sys.argv) > 1 and sys.argv[1] == "auto":
        full = syncer._needs_full()
        days = settings.sync_days if full else settings.refresh_days
    else:
        days = int(sys.argv[1]) if len(sys.argv) > 1 else settings.sync_days
        full = days >= settings.sync_days
    started = time.time()
    print(f"최근 {days}일치 받는 중…")
    syncer.sync(days, full=full, log=print)
    print(f"완료 {time.time() - started:.0f}초 | {store.stats()} | 오류: {syncer.state['error']}")
    if syncer.state["error"] or syncer.state["done"] != days:
        sys.exit(1)


if __name__ == "__main__":
    main()
