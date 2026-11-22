import json

from repositories.db import Database

# 災害潛勢區域（目前是土石流潛勢溪流影響範圍）。距離查詢一樣轉成 geography，索引也建在同一個運算式上
HAZARD_SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS hazard_zones (
        id SERIAL PRIMARY KEY,
        hazard_type VARCHAR(40) NOT NULL,
        ref_no VARCHAR(40),
        county VARCHAR(20),
        town VARCHAR(20),
        village VARCHAR(20),
        risk VARCHAR(10),
        households INTEGER,
        geom GEOMETRY(MultiPolygon, 4326)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_hazard_zones_geog ON hazard_zones USING GIST ((geom::geography))",
    "CREATE INDEX IF NOT EXISTS idx_hazard_zones_type ON hazard_zones (hazard_type)",
)

# 路線避開災害範圍用的三張表／視圖：
# road_hazards   哪些道路邊穿過哪一個潛勢範圍（靜態，同步時由 ST_Intersects 重算）
# hazard_alerts  即時警戒（目前是農村水保署的土石流警戒），定期抓取，過期自動失效
# road_closures  目前生效的道路限制：警戒範圍內的道路邊，紅色警戒封閉、黃色警戒繞行。
#                是視圖，警戒或潛勢範圍任一邊更新就自動反映，不會有兩份資料對不上的時候。
#                之後接其他來源（公路局阻斷、淹水感測器）就在這裡多 UNION 一段
ROUTE_HAZARD_SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS road_hazards (
        edge_id INTEGER NOT NULL,
        hazard_type VARCHAR(40) NOT NULL,
        ref_no VARCHAR(40) NOT NULL,
        risk VARCHAR(10),
        inside_ratio REAL NOT NULL DEFAULT 1,
        PRIMARY KEY (hazard_type, ref_no, edge_id)
    )
    """,
    # 一條邊常常只有一小段碰到範圍（山區一條邊可長達數公里）：加權與「經過範圍多長」都只算落在範圍內的比例
    "ALTER TABLE road_hazards ADD COLUMN IF NOT EXISTS inside_ratio REAL NOT NULL DEFAULT 1",
    "CREATE INDEX IF NOT EXISTS idx_road_hazards_edge ON road_hazards (edge_id)",
    """
    CREATE TABLE IF NOT EXISTS hazard_alerts (
        source VARCHAR(40) NOT NULL,
        hazard_type VARCHAR(40) NOT NULL,
        ref_no VARCHAR(40) NOT NULL,
        level VARCHAR(10) NOT NULL CHECK (level IN ('red', 'yellow')),
        county VARCHAR(20),
        town VARCHAR(20),
        village VARCHAR(40),
        issued_at TIMESTAMPTZ,
        fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (source, ref_no)
    )
    """,
    """
    CREATE OR REPLACE VIEW road_closures AS
    SELECT rh.edge_id,
           CASE a.level WHEN 'red' THEN 'closed' ELSE 'caution' END AS level,
           a.source, a.ref_no, a.issued_at, a.expires_at, rh.inside_ratio
    FROM hazard_alerts a
    JOIN road_hazards rh ON rh.hazard_type = a.hazard_type AND rh.ref_no = a.ref_no
    WHERE a.expires_at > now()
    """,
)


class HazardQueries(Database):
    """災害潛勢區域、道路與範圍的交集、即時警戒"""

    HAZARD_INSERT_SQL = """
        INSERT INTO hazard_zones (hazard_type, ref_no, county, town, village, risk, households, geom)
        VALUES (%s, %s, %s, %s, %s, %s, %s,
                ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), 3)));
    """

    def replace_hazard_zones(self, hazard_type: str, features: list[dict]) -> int:
        """
        整批換掉某一類災害潛勢區域，在同一個交易裡刪舊寫新：寫到一半失敗會整批回滾，
        不會留下「舊的刪了、新的只寫一半」的狀態。官方多邊形偶有自相交，用 ST_MakeValid 修正
        """
        try:
            with self._cursor() as cursor:
                cursor.execute("DELETE FROM hazard_zones WHERE hazard_type = %s", (hazard_type,))
                for f in features:
                    p = f["properties"]
                    cursor.execute(self.HAZARD_INSERT_SQL, (
                        hazard_type, p.get("debris_no"), p.get("county"), p.get("town"), p.get("village"),
                        p.get("risk"), p.get("households"), json.dumps(f["geometry"]),
                    ))
            return len(features)
        except Exception as e:
            raise RuntimeError(f"replace_hazard_zones 失敗：{e}") from e

    def shelter_hazard_distances(self, hazard_type: str, within_m: float) -> dict[str, list[tuple[str, float]]]:
        """
        距離災害潛勢區域 within_m 公尺內的避難所 → [(區域編號, 距離公尺)]，由近到遠。
        距離 0 代表位在區域內。用 geography 算公尺，兩邊的 geography 運算式索引才會被採用
        """
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    """
                    SELECT s.name, z.ref_no, ST_Distance(z.geom::geography, s.geom::geography) AS d
                    FROM shelters s
                    JOIN hazard_zones z
                      ON z.hazard_type = %s
                     AND ST_DWithin(z.geom::geography, s.geom::geography, %s)
                    ORDER BY s.name, d, z.ref_no
                    """,
                    (hazard_type, within_m),
                )
                result: dict[str, list[tuple[str, float]]] = {}
                for name, ref, distance in cursor.fetchall():
                    result.setdefault(name, []).append((ref, float(distance)))
                return result
        except Exception as e:
            raise RuntimeError(f"shelter_hazard_distances 失敗：{e}") from e

    def rebuild_road_hazards(self) -> int:
        """
        重算哪些道路邊穿過哪一個潛勢範圍，回傳筆數。路網或潛勢範圍任一邊更新都要重算；
        全東部 11 萬多條邊對 531 個範圍做 ST_Intersects 不到一秒，所以每次同步直接重算，不另外比對版本。
        同一條溪流可能有好幾塊多邊形，(災害, 範圍編號, 邊) 只留一筆
        """
        try:
            with self._cursor() as cursor:
                cursor.execute("TRUNCATE road_hazards")
                # 同一條溪流的幾塊多邊形先合併，比例才不會重複計算
                cursor.execute(
                    """
                    INSERT INTO road_hazards (edge_id, hazard_type, ref_no, risk, inside_ratio)
                    SELECT r.id, z.hazard_type, z.ref_no, z.risk,
                           LEAST(1, ST_Length(ST_Intersection(r.geom, z.geom)::geography)
                                    / NULLIF(ST_Length(r.geom::geography), 0))
                    FROM roads r
                    JOIN (
                        SELECT hazard_type, ref_no, MAX(risk) AS risk, ST_Union(geom) AS geom
                        FROM hazard_zones WHERE ref_no IS NOT NULL
                        GROUP BY hazard_type, ref_no
                    ) z ON ST_Intersects(r.geom, z.geom)
                    WHERE ST_Length(r.geom) > 0
                    """
                )
                return cursor.rowcount
        except Exception as e:
            raise RuntimeError(f"rebuild_road_hazards 失敗：{e}") from e

    def replace_alerts(self, source: str, hazard_type: str, alerts: list[dict], ttl_minutes: int) -> int:
        """
        換掉某個來源的全部警戒（來源每次給的都是完整的目前狀態，解除的警戒就是不在清單裡）。
        每筆警戒 ttl_minutes 分鐘後失效：抓取一直失敗時，舊警戒不會永遠擋著道路
        """
        try:
            with self._cursor() as cursor:
                cursor.execute("DELETE FROM hazard_alerts WHERE source = %s", (source,))
                for a in alerts:
                    cursor.execute(
                        """
                        INSERT INTO hazard_alerts
                            (source, hazard_type, ref_no, level, county, town, village, issued_at, expires_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now() + make_interval(mins => %s))
                        ON CONFLICT (source, ref_no) DO UPDATE SET
                            level = CASE WHEN EXCLUDED.level = 'red' THEN 'red' ELSE hazard_alerts.level END
                        """,
                        (source, hazard_type, a["ref_no"], a["level"], a.get("county"), a.get("town"),
                         a.get("village"), a.get("issued_at"), ttl_minutes),
                    )
            return len(alerts)
        except Exception as e:
            raise RuntimeError(f"replace_alerts 失敗：{e}") from e

    def active_alerts(self) -> list[dict]:
        """目前生效的警戒，附上範圍內被封閉或要繞行的道路長度（公尺）"""
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    """
                    SELECT a.source, a.hazard_type, a.ref_no, a.level, a.county, a.town, a.village,
                           a.issued_at, a.fetched_at, a.expires_at,
                           COUNT(rh.edge_id), COALESCE(SUM(r.cost * rh.inside_ratio), 0)
                    FROM hazard_alerts a
                    LEFT JOIN road_hazards rh ON rh.hazard_type = a.hazard_type AND rh.ref_no = a.ref_no
                    LEFT JOIN roads r ON r.id = rh.edge_id
                    WHERE a.expires_at > now()
                    GROUP BY a.source, a.hazard_type, a.ref_no, a.level, a.county, a.town, a.village,
                             a.issued_at, a.fetched_at, a.expires_at
                    ORDER BY a.level DESC, a.ref_no
                    """
                )
                keys = ("source", "hazard_type", "ref_no", "level", "county", "town", "village",
                        "issued_at", "fetched_at", "expires_at", "edges", "road_m")
                return [dict(zip(keys, row, strict=True)) for row in cursor.fetchall()]
        except Exception as e:
            raise RuntimeError(f"active_alerts 失敗：{e}") from e
