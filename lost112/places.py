"""보관장소(depPlace) 해석, 원래 습득역 추적, 노선 정보, 물품 분류.

실제 보관장소 표기 (2026-11 데이터 기준)
- 역:          '사당역(2호선)', '개화역(9호선)', '서울역(한국철도공사)', '광교역(신분당선)',
               '서울역(GTX-A)', '수원역(분당선)(한국철도공사)', '8호선(동구릉역)',
               '인천대공원관리역(인천지하철2호선)', '고속철도 수서역(한국철도공사)', '부산역(부산교통공사)'
- 유실물센터:  '시청유실물센터', '충무로유실물센터', '왕십리유실물센터', '태릉유실물센터',
               '동작유실물센터(9호선 1단계)', '신분당선 유실물센터(양재역)', '인천시청역(유실물센터)',
               '정부청사역유실물센터', '초지유실물센터(서해철도)', '대구교통공사'
- 그 외:       '다이소(강남역점)', '(주)신세계(강남점)' → 지하철이 아니므로 제외

원래 습득역
- 습득물 관리번호(atcId) 앞 8자리(V0002024)는 처음 등록한 기관 번호다.
  역에서 등록한 뒤 유실물센터로 옮겨도 번호는 그대로라서, 역 보관 물건으로 '기관번호 → 역'을 배워
  센터 보관 물건의 원래 역을 찾는다.
- 상세정보 특이사항(uniq)의 '신도림역(2호선)에서는 [26-11-13] …' 문구로도 확인한다.
"""
import re

STATION_RE = re.compile(r"^(?P<name>[^()]+?)역\s*\((?P<op>[^()]+)\)(?:\s*\((?P<op2>[^()]+)\))?$")
LINE_FIRST_RE = re.compile(r"^(?P<op>\d+호선)\s*\((?P<name>[^()]+?)역\)$")
CENTER_RE = re.compile(r"^(?P<name>.*?)\s*유실물\s*센터")
UNIQ_ORIGIN_RE = re.compile(r"^\s*(?P<place>.+?)에서는\s*\[")
LINE_NUM_RE = re.compile(r"(\d+)\s*호선")
TRANSIT_OP_RE = re.compile(r"호선|철도|선$|지하철|교통|메트로|공사|경전철|레일|공항|GTX")

# 서울교통공사 유실물센터가 맡는 노선 (서울시 안내 기준)
SEOUL_METRO_CENTERS = {
    "시청": ("1호선", "2호선"),
    "충무로": ("3호선", "4호선"),
    "왕십리": ("5호선", "8호선"),
    "태릉": ("6호선", "7호선"),
}
OTHER_CENTERS = {
    "동작": ("9호선",),
    "신분당선": ("신분당선",),
    "인천시청": ("인천1호선", "인천2호선"),
    "초지": ("서해철도",),
}
CENTER_LINES = {**SEOUL_METRO_CENTERS, **OTHER_CENTERS}
CENTER_FOR_LINE = {}
for _name, _lines in CENTER_LINES.items():
    for _ln in _lines:
        CENTER_FOR_LINE.setdefault(_ln, _name)

LINE_ALIASES = {"분당선": "수인분당선", "서해선": "서해철도"}
LINE_COLORS = {
    "1호선": "#0052A4", "2호선": "#00A84D", "3호선": "#EF7C1C", "4호선": "#00A5DE",
    "5호선": "#996CAC", "6호선": "#CD7C2F", "7호선": "#747F00", "8호선": "#E6186C",
    "9호선": "#BDB092", "신분당선": "#D4003B", "공항철도": "#0090D2", "GTX-A": "#9A6292",
    "인천1호선": "#7CA8D5", "인천2호선": "#ED8B00", "경의중앙선": "#77C4A3",
    "수인분당선": "#FABE00", "경춘선": "#0C8E72", "우이신설선": "#B0CE18",
    "신림선": "#6789CA", "김포골드라인": "#A17800", "서해철도": "#81A914",
}
SPECIAL_LABELS = {
    "신분당선": "신분당", "공항철도": "공항", "한국철도공사": "코레일", "GTX-A": "GTX",
    "수인분당선": "수인분당", "서해철도": "서해", "부산교통공사": "부산", "인천교통공사": "인천",
    "대구교통공사": "대구", "광주1호선": "광주1",
}
# '전체로 넓히기'가 의미 없는 운영사 단위 (전국 코레일 역 전체 등)
NOT_A_LINE = {"한국철도공사"}
NEUTRAL = "#6B7280"


def normalize_station(text: str) -> str:
    """'강남역 ' → '강남', '서울역' → '서울'."""
    s = re.sub(r"\s+", "", text or "")
    if len(s) > 1 and s.endswith("역"):
        s = s[:-1]
    return s


def normalize_line(op: str) -> str:
    op = re.sub(r"\s+", "", op)
    m = re.fullmatch(r"인천(?:지하철)?(\d)호선", op)
    if m:
        return f"인천{m.group(1)}호선"
    return LINE_ALIASES.get(op, op)


def _clean_station_name(name: str) -> str:
    name = re.sub(r"^(고속철도|KTX)\s*", "", name.strip())
    if name.endswith("관리") and len(name) > 3:  # 인천 '가정관리역' 같은 관리역
        name = name[:-2]
    return name


def parse_place(dep: str) -> tuple[str, str, tuple[str, ...], bool]:
    """보관장소 → (종류 'station'|'center'|'other', 역/센터 이름, 노선들, 지하철 여부)."""
    dep = (dep or "").strip()
    m = STATION_RE.match(dep)
    if m:
        name = _clean_station_name(m.group("name"))
        op = m.group("op").strip()
        if "유실물" in op:
            return "center", name, CENTER_LINES.get(name, ()), True
        return "station", name, (normalize_line(op),), bool(TRANSIT_OP_RE.search(op))
    m = LINE_FIRST_RE.match(dep)
    if m:
        return "station", _clean_station_name(m.group("name")), (m.group("op"),), True
    m = CENTER_RE.match(dep)
    if m:
        name = re.sub(r"역$", "", m.group("name").strip()) or dep
        if name in CENTER_LINES:
            lines = CENTER_LINES[name]
        elif LINE_NUM_RE.search(dep):
            lines = tuple(f"{n}호선" for n in LINE_NUM_RE.findall(dep))
        else:
            lines = ()
        transit = bool(lines) or bool(re.search(r"역|철도|지하철|메트로|교통", dep))
        return "center", name, lines, transit
    if dep.endswith("교통공사"):  # '대구교통공사' 처럼 운영사 이름만 있는 경우
        return "center", dep, (), True
    return "other", "", (), False


def parse_uniq_origin(uniq: str) -> tuple[str, tuple[str, ...]] | None:
    """상세 특이사항 '신도림역(2호선)에서는 [26-11-13] …' → ('신도림', ('2호선',))."""
    m = UNIQ_ORIGIN_RE.match(uniq or "")
    if not m:
        return None
    kind, name, lines, transit = parse_place(m.group("place"))
    return (name, lines) if kind == "station" and transit else None


def org_of(atc_id: str) -> str:
    return (atc_id or "")[:8]


# ---- 노선 표시 -------------------------------------------------------------
def _fg_for(hex_color: str) -> str:
    """배경색 위 글자색: 흰 글자 대비가 3:1 이상이면 흰색, 아니면 진한 회색."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    lum = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return "#FFFFFF" if 1.05 / (lum + 0.05) >= 3.0 else "#111827"


def line_label(line: str) -> str:
    if line in SPECIAL_LABELS:
        return SPECIAL_LABELS[line]
    m = re.fullmatch(r"(\d+)호선", line)
    if m:
        return m.group(1)
    m = re.fullmatch(r"(\D+?)(\d+)호선", line)
    if m:
        return f"{m.group(1)[:2]}{m.group(2)}"
    if line.endswith("선"):
        return line[:-1][:4]
    return line[:4]


def line_name(line: str) -> str:
    """화면에 쓰는 이름: '한국철도공사' → '코레일'."""
    return "코레일" if line == "한국철도공사" else line


def line_meta(line: str) -> dict:
    color = LINE_COLORS.get(line, NEUTRAL)
    center = CENTER_FOR_LINE.get(line)
    return {
        "line": line,
        "name": line_name(line),
        "label": line_label(line),
        "color": color,
        "fg": _fg_for(color),
        "center": f"{center} 유실물센터" if center and center != line else ("신분당선 유실물센터" if center else None),
        "widenable": line not in NOT_A_LINE,
    }


def line_sort_key(line: str):
    m = re.fullmatch(r"(\d+)호선", line)
    if m:
        return (0, int(m.group(1)), line)
    return (1 if line in LINE_COLORS else 2, 0, line)


def split_lines(value: str | None) -> list[str]:
    return [ln for ln in (value or "").strip(",").split(",") if ln]


def join_lines(lines) -> str:
    lines = [ln for ln in lines if ln]
    return f",{','.join(lines)}," if lines else ""


# ---- 물품 분류 -------------------------------------------------------------
CATEGORIES = {
    "wallet": "지갑·카드",
    "phone": "휴대폰",
    "bag": "가방",
    "umbrella": "우산",
    "device": "이어폰·기기",
    "etc": "기타",
}
_DEVICE_WORDS = ("이어폰", "에어팟", "버즈", "헤드폰", "헤드셋", "충전기", "보조배터리", "배터리",
                 "태블릿", "아이패드", "갤럭시탭", "노트북", "워치", "카메라", "전자사전", "게임기")
_PHONE_WORDS = ("휴대폰", "핸드폰", "스마트폰", "아이폰", "갤럭시", "폴더폰")
_WALLET_WORDS = ("지갑", "카드", "신분증", "면허증")
_BAG_WORDS = ("가방", "백팩", "배낭", "파우치", "쇼핑백", "캐리어")


def classify(prdt_cl: str, name: str) -> str:
    """prdtClNm('휴대폰 > 아이폰')과 물품명으로 화면의 6개 분류 중 하나를 고른다."""
    top = (prdt_cl or "").split(">")[0].strip()
    nm = name or ""
    if "우산" in nm or "양산" in nm or "우산" in (prdt_cl or ""):
        return "umbrella"
    if any(w in nm for w in _DEVICE_WORDS):  # '갤럭시 버즈'가 휴대폰으로 가지 않도록 먼저
        return "device"
    if top in ("지갑", "카드", "증명서"):
        return "wallet"
    if top == "휴대폰":
        return "phone"
    if top in ("가방", "쇼핑백"):
        return "bag"
    if top in ("전자기기", "컴퓨터", "카메라"):
        return "device"
    if any(w in nm for w in _PHONE_WORDS):
        return "phone"
    if any(w in nm for w in _WALLET_WORDS):
        return "wallet"
    if any(w in nm for w in _BAG_WORDS):
        return "bag"
    return "etc"
