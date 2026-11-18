"""data.go.kr 경찰청_포털기관 습득물정보 조회 서비스 클라이언트 (XML 응답).

확인된 사실 (2026-11 실제 호출 기준)
- 응답은 XML만 온다 (_type=json 무시).
- numOfRows 5000 까지 한 번에 받을 수 있다.
- 기간 검색 결과는 날짜순이 아니다 → 하루 단위로 끊어서 전부 받는다.
- 오류는 HTTP 4xx + <returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg> 형태.
"""
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

BASE = "https://apis.data.go.kr/1320000/LosPtfundInfoInqireService"
PAGE_SIZE = 5000

FRIENDLY_ERRORS = {
    "SERVICE_KEY_IS_NOT_REGISTERED_ERROR": "등록되지 않은 서비스키예요. data.go.kr 활용신청 상태와 .env 의 키를 확인하세요.",
    "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR": "오늘 API 호출 한도를 넘었어요. 한도가 풀리면 자동으로 다시 받아요.",
    "SERVICE_ACCESS_DENIED_ERROR": "서비스 접근이 거부됐어요. 활용신청 승인 상태를 확인하세요.",
    "DEADLINE_HAS_EXPIRED_ERROR": "API 활용 기간이 끝났어요. data.go.kr 에서 연장 신청하세요.",
    "SERVICE_KEY_IS_NOT_REGISTERED": "등록되지 않은 서비스키예요.",
}
# 이 오류들은 잠시 뒤 다시 시도해도 해결되지 않는다.
FATAL_CODES = {
    "SERVICE_KEY_IS_NOT_REGISTERED_ERROR",
    "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR",
    "SERVICE_ACCESS_DENIED_ERROR",
    "DEADLINE_HAS_EXPIRED_ERROR",
}


class ApiError(Exception):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code

    @property
    def fatal(self) -> bool:
        return self.code in FATAL_CODES


def _items(root: ET.Element) -> list[dict]:
    return [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]


class Lost112Client:
    def __init__(self, service_key: str, timeout: float = 60):
        self._key = service_key
        self._timeout = timeout

    def _get(self, op: str, params: dict) -> ET.Element:
        # URL 에는 키가 들어가므로 로그나 예외 메시지에 URL 을 남기지 않는다.
        url = f"{BASE}/{op}?" + urllib.parse.urlencode({"serviceKey": self._key, **params})
        req = urllib.request.Request(url, headers={"User-Agent": "lost-in-subway/1.0"})
        body = None
        last_error = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as r:
                    body = r.read()
                break
            except urllib.error.HTTPError as e:
                body = e.read()
                if e.code < 500:
                    break
                last_error = f"HTTP {e.code}"
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last_error = type(e).__name__
            time.sleep(1.5 * (attempt + 1))
        if body is None:
            raise ApiError(f"data.go.kr 에 연결하지 못했어요 ({last_error})")
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            raise ApiError("API 응답을 해석하지 못했어요") from None
        auth = (root.findtext(".//returnAuthMsg") or "").strip()
        if auth and auth != "NORMAL_CODE":
            raise ApiError(FRIENDLY_ERRORS.get(auth, auth), auth)
        code = (root.findtext(".//resultCode") or "").strip()
        if code and code != "00":
            msg = (root.findtext(".//resultMsg") or "").strip()
            raise ApiError(msg or f"API 오류 코드 {code}", code)
        return root

    def list_day(self, ymd: str, page: int = 1, rows: int = PAGE_SIZE) -> tuple[int, list[dict]]:
        """하루(습득일 기준)치 습득물 목록. ymd = 'YYYYMMDD'."""
        root = self._get("getPtLosfundInfoAccToClAreaPd", {
            "START_YMD": ymd, "END_YMD": ymd, "pageNo": page, "numOfRows": rows,
        })
        total = int((root.findtext(".//totalCount") or "0").strip() or 0)
        return total, _items(root)

    def detail(self, atc_id: str, fd_sn: str) -> dict | None:
        root = self._get("getPtLosfundDetailInfo", {"ATC_ID": atc_id, "FD_SN": fd_sn or "1"})
        items = _items(root)
        return items[0] if items else None
