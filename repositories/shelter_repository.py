"""
ShelterRepository 是資料庫的唯一入口，由各類查詢組合而成：
  db.py          連線池與 cursor
  shelters.py    避難所（同步、收容人數、半徑與最近距離）
  hazards.py     災害潛勢區域、道路與範圍的交集、即時警戒
  roads.py       路網匯入、吸附、路線成本與路徑
  assignment.py  疏散分配的最小成本流
schema 一次建好，放在這裡
"""
import logging

from repositories.assignment import AssignmentQueries
from repositories.hazards import HAZARD_SCHEMA_SQL, ROUTE_HAZARD_SCHEMA_SQL, HazardQueries
from repositories.roads import ROAD_SCHEMA_SQL, RoadQueries
from repositories.shelters import GEOGRAPHY_INDEX_SQL, SHELTER_DETAIL_COLUMNS_SQL, ShelterQueries

logger = logging.getLogger(__name__)


class ShelterRepository(ShelterQueries, HazardQueries, RoadQueries, AssignmentQueries):
    def ensure_schema(self):
        try:
            with self._cursor() as cursor:
                cursor.execute("ALTER TABLE shelters ADD COLUMN IF NOT EXISTS address VARCHAR(200) DEFAULT ''")
                for statement in SHELTER_DETAIL_COLUMNS_SQL:
                    cursor.execute(statement)
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_shelters_geom ON shelters USING GIST (geom)")
                cursor.execute(GEOGRAPHY_INDEX_SQL)
                for statement in HAZARD_SCHEMA_SQL + ROAD_SCHEMA_SQL + ROUTE_HAZARD_SCHEMA_SQL:
                    cursor.execute(statement)
        except Exception as e:
            raise RuntimeError(f"ensure_schema 失敗：{e}") from e
        # 路網是附加功能：映像沒有 pgRouting（還在用舊的 postgis 映像）時只記警告，
        # 距離查詢會退回直線距離。放在自己的交易裡，失敗才不會連帶回滾上面的 schema
        try:
            with self._cursor() as cursor:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS pgrouting")
        except Exception as e:
            logger.warning("無法啟用 pgRouting，距離一律用直線計算：%s", e)
