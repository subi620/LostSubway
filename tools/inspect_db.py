"""적재된 DB 를 훑어보고 분류 규칙이 맞는지 확인."""
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(errors="replace")
db = sqlite3.connect(Path(__file__).resolve().parent.parent / "data" / "lost112.db")
q = lambda sql, *a: db.execute(sql, a).fetchall()


def show(title, rows, fmt=lambda r: "  ".join(str(x) for x in r)):
    print(f"\n## {title}")
    for r in rows:
        print("   ", fmt(r))


show("종류별", q("SELECT kind, is_transit, COUNT(*) FROM items GROUP BY 1,2 ORDER BY 3 DESC"))
show("역 노선(lines) 상위", q("SELECT lines, COUNT(*) FROM items WHERE kind='station' GROUP BY 1 ORDER BY 2 DESC LIMIT 40"))
show("지하철 아님으로 빠진 '역(…)' 표기", q("SELECT dep_place, COUNT(*) FROM items WHERE kind='station' AND is_transit=0 GROUP BY 1 ORDER BY 2 DESC LIMIT 15"))
show("유실물센터", q("SELECT dep_place, station, lines, is_transit, COUNT(*) FROM items WHERE kind='center' GROUP BY 1 ORDER BY 5 DESC LIMIT 25"))
show("other 중 지하철 같아 보이는 것", q("SELECT dep_place, COUNT(*) FROM items WHERE kind='other' AND (dep_place LIKE '%역%' OR dep_place LIKE '%호선%' OR dep_place LIKE '%철도%' OR dep_place LIKE '%지하철%' OR dep_place LIKE '%교통%') GROUP BY 1 ORDER BY 2 DESC LIMIT 25"))
show("분류 대분류(지하철)", q("SELECT trim(substr(prdt_cl,1,instr(prdt_cl||'>','>')-1)) t, COUNT(*) FROM items WHERE is_transit=1 GROUP BY t ORDER BY 2 DESC"))
show("화면 분류(cat)", q("SELECT cat, COUNT(*) FROM items WHERE is_transit=1 GROUP BY 1 ORDER BY 2 DESC"))
show("기타(etc) 안의 세부 분류 상위", q("SELECT prdt_cl, COUNT(*) FROM items WHERE is_transit=1 AND cat='etc' GROUP BY 1 ORDER BY 2 DESC LIMIT 25"))
show("이름에 이어폰/에어팟/버즈", q("SELECT prdt_cl, COUNT(*) FROM items WHERE is_transit=1 AND (name LIKE '%이어폰%' OR name LIKE '%에어팟%' OR name LIKE '%버즈%') GROUP BY 1 ORDER BY 2 DESC LIMIT 10"))
show("우산", q("SELECT prdt_cl, name, COUNT(*) FROM items WHERE cat='umbrella' GROUP BY 1,2 ORDER BY 3 DESC LIMIT 10"))
show("강남/사당/홍대입구/시청", q("SELECT station, lines, kind, COUNT(*) FROM items WHERE is_transit=1 AND station IN ('강남','사당','홍대입구','시청','서울','잠실') GROUP BY 1,2,3 ORDER BY 1"))
show("사진 있는 비율", q("SELECT SUM(img != ''), COUNT(*) FROM items WHERE is_transit=1"))
show("역 수", q("SELECT COUNT(DISTINCT station) FROM items WHERE kind='station' AND is_transit=1"))
