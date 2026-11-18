"""`.env` 와 환경변수에서 설정을 읽는다."""
import os
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_KEYS = ("LOST112_SERVICE_KEY", "PORT", "SYNC_DAYS", "REFRESH_DAYS", "REFRESH_MINUTES")


def _read_env_file(path: Path) -> dict:
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def _int(value, default, lo, hi):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    service_key: str
    port: int
    sync_days: int        # DB에 보관할 기간(일). 화면의 최대 검색 기간과 같다.
    refresh_days: int     # 주기 갱신 때 다시 받을 최근 기간(일)
    refresh_minutes: int  # 주기 갱신 간격(분)
    db_path: Path

    @classmethod
    def load(cls) -> "Settings":
        env = _read_env_file(ROOT / ".env")
        env.update({k: os.environ[k] for k in _KEYS if os.environ.get(k)})
        key = env.get("LOST112_SERVICE_KEY", "").strip()
        if "%" in key:  # Encoding 키를 넣은 경우
            key = urllib.parse.unquote(key)
        return cls(
            service_key=key,
            port=_int(env.get("PORT"), 5600, 1, 65535),
            sync_days=_int(env.get("SYNC_DAYS"), 30, 3, 90),
            refresh_days=_int(env.get("REFRESH_DAYS"), 5, 1, 30),
            refresh_minutes=_int(env.get("REFRESH_MINUTES"), 60, 10, 24 * 60),
            db_path=ROOT / "data" / "lost112.db",
        )
