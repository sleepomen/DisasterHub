import json
import logging
import threading
import time
from contextlib import contextmanager

from psycopg2 import OperationalError
from psycopg2.pool import PoolError, ThreadedConnectionPool

import config
from models.shelter import NearbyShelter, Shelter

MAX_RETRIES = 3
RETRY_DELAY = 2  # 秒

# 連線池被同時進來的請求佔滿時，等一下再拿而不是立刻 500；超過這個秒數才放棄
POOL_WAIT_SECONDS = 5.0
POOL_RETRY_INTERVAL = 0.1

logger = logging.getLogger(__name__)
_pool = None
_pool_lock = threading.Lock()


def _get_pool(conn_params):
    """
    建立（或取回）全域連線池。鎖只包住「建池」這一步，重試之間的等待放在鎖外面，
    資料庫還沒起來時其他請求執行緒才不會跟著被鎖卡住好幾秒。
    """
    global _pool
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        with _pool_lock:
            if _pool is not None:
                return _pool
            try:
                _pool = ThreadedConnectionPool(config.DB_POOL_MIN, config.DB_POOL_MAX, **conn_params)
                return _pool
            except OperationalError as e:
                last_error = e
                logger.warning("DB 連線失敗（第 %d 次）：%s", attempt, e)
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)
    raise RuntimeError(f"資料庫連線失敗，已重試 {MAX_RETRIES} 次：{last_error}")


def _acquire(pool):
    """從池子拿連線；池子滿了就在 POOL_WAIT_SECONDS 內反覆重試"""
    deadline = time.monotonic() + POOL_WAIT_SECONDS
    while True:
        try:
            return pool.getconn()
        except PoolError as e:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"資料庫連線池已滿，等待 {POOL_WAIT_SECONDS:g} 秒仍拿不到連線：{e}") from e
            time.sleep(POOL_RETRY_INTERVAL)


# 半徑與最近距離查詢都用 geom::geography（才能用公尺算真實距離），但 idx_shelters_geom 建在
# geometry 上，EXPLAIN 證實這兩個查詢一律 Seq Scan——關掉 seqscan 也一樣。運算式索引要跟查詢裡的
# 運算式完全相同才會被採用，所以這裡建的就是 (geom::geography)
GEOGRAPHY_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_shelters_geog ON shelters USING GIST ((geom::geography))"

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

# 路網（pgRouting 的邊表）。cost / reverse_cost 是公尺；兩個方向一樣長，不考慮單行道——
# 疏散時步行與車行都有，單行道對步行沒有意義。KNN 吸附用 geometry 的 GiST 索引
ROAD_SCHEMA_SQL = (
    """
    CREATE TABLE IF NOT EXISTS roads (
        id INTEGER PRIMARY KEY,
        source INTEGER NOT NULL,
        target INTEGER NOT NULL,
        cost DOUBLE PRECISION NOT NULL,
        reverse_cost DOUBLE PRECISION NOT NULL,
        highway VARCHAR(20),
        geom GEOMETRY(LineString, 4326)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_roads_geom ON roads USING GIST (geom)",
    # 大檔案每次啟動都重灌太慢：記下匯入的是哪一版，內容沒變就跳過
    """
    CREATE TABLE IF NOT EXISTS dataset_versions (
        name VARCHAR(40) PRIMARY KEY,
        checksum VARCHAR(64) NOT NULL,
        loaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
)

# 點離最近的道路超過這個距離就不吸附，該點不算道路距離（例如海上、深山）
ROAD_SNAP_M = 1000
# 疏散起點是鄉鎮人口重心，只是一個代表點，常常落在田裡或河床上（玉里鎮的重心離最近的道路 3.2 公里）。
# 起點可以吸附得比較遠，那一段走到路上的距離照樣算進道路距離
ORIGIN_SNAP_M = 5000
# 路徑可能繞出起訖點的外接框（沿海岸繞過山）。框往外擴大這麼多度，再至少擴大框本身的一半
ROUTE_MARGIN_DEG = 0.05
FRACTION_EPS = 1e-6

SELECT_COLUMNS ="name, capacity, current_ppl, ST_Y(geom::geometry), ST_X(geom::geometry), COALESCE(address, '')"


def _row_to_shelter(row) -> Shelter:
    return Shelter(
        name=row[0],
        capacity=row[1],
        current_people=row[2],
        lat=row[3],
        lon=row[4],
        address=row[5],
    )


class ShelterRepository:
    def __init__(self):
        self.conn_params = {
            "dbname": config.POSTGRES_DB,
            "user": config.POSTGRES_USER,
            "password": config.POSTGRES_PASSWORD,
            "host": config.POSTGRES_HOST,
            "port": config.POSTGRES_PORT,
            # 資料庫卡住時請求要能在幾秒內失敗，readiness 才會如實回報，而不是所有人一起無限等
            "connect_timeout": config.DB_CONNECT_TIMEOUT,
            "options": f"-c statement_timeout={config.DB_STATEMENT_TIMEOUT_MS}",
        }

    @contextmanager
    def _cursor(self):
        pool = _get_pool(self.conn_params)
        conn = _acquire(pool)
        try:
            with conn.cursor() as cursor:
                yield cursor
            conn.commit()
        except Exception:
            if not conn.closed:
                conn.rollback()
            raise
        finally:
            pool.putconn(conn, close=bool(conn.closed))

    def ping(self) -> None:
        """readiness 用：確認連線池拿得到連線，而且資料庫真的答得出來"""
        with self._cursor() as cursor:
            cursor.execute("SELECT 1")

    def ensure_schema(self):
        try:
            with self._cursor() as cursor:
                cursor.execute("ALTER TABLE shelters ADD COLUMN IF NOT EXISTS address VARCHAR(200) DEFAULT ''")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_shelters_geom ON shelters USING GIST (geom)")
                cursor.execute(GEOGRAPHY_INDEX_SQL)
                for statement in HAZARD_SCHEMA_SQL + ROAD_SCHEMA_SQL:
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

    # 同步只負責「靜態」欄位（容量 / 地址 / 座標）。
    # current_ppl 是模擬回寫的即時狀態，重啟或手動同步都不能把它洗掉；
    # 只有在容量縮小到低於目前人數時才往下壓，避免出現負的剩餘空間。
    UPSERT_SQL = """
        INSERT INTO shelters (name, capacity, current_ppl, address, geom)
        VALUES (%s, %s, %s, %s, ST_SetSRID(ST_Point(%s, %s), 4326))
        ON CONFLICT (name) DO UPDATE SET
            capacity = EXCLUDED.capacity,
            current_ppl = LEAST(shelters.current_ppl, EXCLUDED.capacity),
            address = EXCLUDED.address,
            geom = EXCLUDED.geom;
    """

    # 一次更新多筆佔用數；人數夾在 [0, capacity] 之間，名稱不存在的列直接略過
    SET_OCCUPANCY_SQL = """
        UPDATE shelters AS s
        SET current_ppl = LEAST(GREATEST(v.ppl, 0), s.capacity)
        FROM (SELECT unnest(%s::text[]) AS name, unnest(%s::int[]) AS ppl) AS v
        WHERE s.name = v.name;
    """

    @staticmethod
    def _upsert_params(shelter: Shelter):
        return (
            shelter.name,
            shelter.capacity,
            shelter.current_people,
            shelter.address,
            shelter.lon,
            shelter.lat,
        )

    def upsert_shelter(self, shelter: Shelter):
        try:
            with self._cursor() as cursor:
                cursor.execute(self.UPSERT_SQL, self._upsert_params(shelter))
        except Exception as e:
            raise RuntimeError(f"upsert_shelter 失敗：{e}") from e

    def upsert_shelters(self, shelters: list[Shelter]) -> int:
        if not shelters:
            return 0
        params = [self._upsert_params(s) for s in shelters]
        try:
            with self._cursor() as cursor:
                cursor.executemany(self.UPSERT_SQL, params)
        except Exception as e:
            raise RuntimeError(f"upsert_shelters 失敗：{e}") from e
        return len(params)

    def set_occupancy(self, occupancy: dict[str, int]) -> int:
        """
        回寫各避難所目前收容人數（模擬結果 / 重置用）。回傳實際更新的列數。
        """
        if not occupancy:
            return 0
        names = list(occupancy.keys())
        counts = [int(occupancy[n]) for n in names]
        try:
            with self._cursor() as cursor:
                cursor.execute(self.SET_OCCUPANCY_SQL, (names, counts))
                return cursor.rowcount
        except Exception as e:
            raise RuntimeError(f"set_occupancy 失敗：{e}") from e

    # 清掉來源已經移除的避難所。沒有這一步，從 JSON 刪掉一間避難所之後，
    # 資料庫那一列會永遠留著，AI 會繼續推薦一個已經不是避難所的地點；
    # 改名更糟：舊名新名各一列。名稱為 NULL 的髒資料留著不動（NULL <> ALL 不成立）
    DELETE_MISSING_SQL = "DELETE FROM shelters WHERE name <> ALL(%s::text[]);"

    def delete_missing(self, keep_names: list[str]) -> int:
        """刪除名稱不在 keep_names 裡的避難所，回傳刪掉的列數"""
        if not keep_names:
            raise ValueError("delete_missing 需要一份非空的保留清單，空清單會清掉整張表")
        try:
            with self._cursor() as cursor:
                cursor.execute(self.DELETE_MISSING_SQL, (list(keep_names),))
                return cursor.rowcount
        except Exception as e:
            raise RuntimeError(f"delete_missing 失敗：{e}") from e

    def get_all_shelters(self):
        # 固定排序，讓向量索引指紋與前端列表在重啟後保持一致
        try:
            with self._cursor() as cursor:
                cursor.execute(f"SELECT {SELECT_COLUMNS} FROM shelters ORDER BY name")
                return [_row_to_shelter(row) for row in cursor.fetchall()]
        except Exception as e:
            raise RuntimeError(f"get_all_shelters error：{e}") from e

    def get_shelters_in_radius(self, lat: float, lon: float, radius_km: float) -> list[Shelter]:
        """
        使用 PostGIS 找出中心點半徑內的避難所。
        回傳 Shelter 物件（跟 get_all_shelters 一致），轉成 API 的 JSON 由 MapService 負責。
        """
        impacted: list[Shelter] = []
        try:
            with self._cursor() as cursor:
                sql = f"""
                    SELECT {SELECT_COLUMNS}
                    FROM shelters
                    WHERE ST_DWithin(
                        geom,
                        ST_SetSRID(ST_Point(%s, %s), 4326)::geography,
                        %s
                    );
                """
                cursor.execute(sql, (lon, lat, radius_km * 1000))
                impacted = [_row_to_shelter(row) for row in cursor.fetchall()]
        except Exception as e:
            raise RuntimeError(f"get_shelters_in_radius 失敗：{e}") from e
        return impacted

    def get_nearest_shelters(self, lat: float, lon: float, limit: int = 5) -> list[NearbyShelter]:
        """
        使用 PostGIS ST_Distance 找出距離中心點最近的 N 個避難所，依距離排序。
        距離是這次查詢才有的值，所以包成 NearbyShelter 而不是塞進 Shelter。
        """
        nearest: list[NearbyShelter] = []
        try:
            with self._cursor() as cursor:
                sql = f"""
                    SELECT
                        {SELECT_COLUMNS},
                        ROUND(
                            ST_Distance(
                                geom::geography,
                                ST_SetSRID(ST_Point(%s, %s), 4326)::geography
                            )::numeric / 1000, 2
                        ) AS distance_km
                    FROM shelters
                    ORDER BY geom::geography <-> ST_SetSRID(ST_Point(%s, %s), 4326)::geography
                    LIMIT %s;
                """
                cursor.execute(sql, (lon, lat, lon, lat, limit))
                nearest = [
                    NearbyShelter(shelter=_row_to_shelter(row), distance_km=float(row[6]))
                    for row in cursor.fetchall()
                ]
        except Exception as e:
            raise RuntimeError(f"get_nearest_shelters 失敗：{e}") from e
        return nearest

    # ── 災害潛勢區域 ─────────────────────────────────────────
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

    # ── 路網 ─────────────────────────────────────────

    def replace_road_network(self, edges: list[list], checksum: str) -> int | None:
        """
        整批換掉路網，回傳寫入的邊數；內容跟上次匯入的一樣（checksum 相同）就跳過、回 None。
        邊的格式見 tools/build_road_network.py：[source, target, 長度公尺, 道路等級, encoded polyline]。
        在同一個交易裡清空再寫入，寫到一半失敗會整批回滾
        """
        from psycopg2.extras import execute_values

        try:
            with self._cursor() as cursor:
                cursor.execute("SELECT checksum FROM dataset_versions WHERE name = 'roads'")
                row = cursor.fetchone()
                if row and row[0] == checksum:
                    return None
                cursor.execute("TRUNCATE roads")
                execute_values(
                    cursor,
                    "INSERT INTO roads (id, source, target, cost, reverse_cost, highway, geom) VALUES %s",
                    [(i, s, t, length, length, highway, polyline)
                     for i, (s, t, length, highway, polyline) in enumerate(edges, 1)],
                    template="(%s, %s, %s, %s, %s, %s, ST_LineFromEncodedPolyline(%s, 5))",
                    page_size=2000,
                )
                cursor.execute(
                    """
                    INSERT INTO dataset_versions (name, checksum) VALUES ('roads', %s)
                    ON CONFLICT (name) DO UPDATE SET checksum = EXCLUDED.checksum, loaded_at = now()
                    """,
                    (checksum,),
                )
                cursor.execute("ANALYZE roads")
            return len(edges)
        except Exception as e:
            raise RuntimeError(f"replace_road_network 失敗：{e}") from e

    # 每個點吸附到最近的一條道路：邊的編號、在邊上的位置（0～1）、點到路的距離（公尺）。
    # KNN 用 geometry 的 <->（單位是度，東部緯度下經緯度的比例差不到一成，挑最近的一條夠用）。
    # 長度為 0 的邊（簡化後兩端落在同一公尺格內）算不出位置，不拿來吸附
    SNAP_SQL = """
        SELECT t.idx, r.id, ST_LineLocatePoint(r.geom, t.g), ST_Distance(r.geom::geography, t.g::geography)
        FROM (
            SELECT idx, ST_SetSRID(ST_Point(lon, lat), 4326) AS g
            FROM unnest(%s::int[], %s::float8[], %s::float8[]) AS u(idx, lon, lat)
        ) t
        CROSS JOIN LATERAL (
            SELECT id, geom FROM roads WHERE ST_Length(geom) > 0 ORDER BY geom <-> t.g LIMIT 1
        ) r
    """

    def road_distances(
        self, origins: list[tuple[float, float]], targets: list[tuple[float, float]],
        snap_m: float = ROAD_SNAP_M, origin_snap_m: float | None = None,
    ) -> dict[tuple[int, int], float]:
        """
        起點 × 終點的道路距離（公尺），key 是 (起點序號, 終點序號)，座標是 (lat, lon)。
        距離＝起點走到最近道路＋沿路網的最短路徑＋終點離開道路的那一段。
        吸附不到道路（終點離路超過 snap_m、起點超過 origin_snap_m，沒給就跟 snap_m 一樣）
        或路網不連通的組合不會出現在結果裡，由呼叫端退回直線距離
        """
        origin_snap_m = snap_m if origin_snap_m is None else origin_snap_m
        points = list(origins) + list(targets)
        if not origins or not targets:
            return {}
        try:
            with self._cursor() as cursor:
                cursor.execute(self.SNAP_SQL, (
                    list(range(len(points))), [lon for _, lon in points], [lat for lat, _ in points],
                ))
                # 吸附點常常剛好落在路段端點（fraction 0 或 1），pgRouting 3.4 的 withPoints 遇到這種點
                # 會整組回傳空結果（實測花蓮火車站一筆都算不出來）。往內夾一點點，誤差不到一公釐
                snaps = {
                    idx: (edge_id, min(max(float(fraction), FRACTION_EPS), 1 - FRACTION_EPS), float(offset))
                    for idx, edge_id, fraction, offset in cursor.fetchall()
                    if offset <= (origin_snap_m if idx < len(origins) else snap_m)
                }
                starts = [i for i in range(len(origins)) if i in snaps]
                ends = [len(origins) + j for j in range(len(targets)) if len(origins) + j in snaps]
                if not starts or not ends:
                    return {}

                lats = [points[i][0] for i in starts + ends]
                lons = [points[i][1] for i in starts + ends]
                pad_lat = ROUTE_MARGIN_DEG + (max(lats) - min(lats)) / 2
                pad_lon = ROUTE_MARGIN_DEG + (max(lons) - min(lons)) / 2
                # pgRouting 收的是 SQL 字串；放進去的只有這裡算出來的數字，不是使用者的文字
                edges_sql = (
                    "SELECT id, source, target, cost, reverse_cost FROM roads "
                    f"WHERE geom && ST_MakeEnvelope({min(lons) - pad_lon}, {min(lats) - pad_lat}, "
                    f"{max(lons) + pad_lon}, {max(lats) + pad_lat}, 4326)"
                )
                values = ", ".join(f"({i + 1}, {snaps[i][0]}, {snaps[i][1]})" for i in starts + ends)
                points_sql = f"SELECT pid, edge_id, fraction FROM (VALUES {values}) AS p(pid, edge_id, fraction)"
                cursor.execute(
                    """
                    SELECT start_pid, end_pid, agg_cost
                    FROM pgr_withPointsCost(%s, %s, %s::bigint[], %s::bigint[], directed := false)
                    """,
                    (edges_sql, points_sql, [-(i + 1) for i in starts], [-(i + 1) for i in ends]),
                )
                routed = {(abs(s) - 1, abs(e) - 1): float(cost) for s, e, cost in cursor.fetchall()}
        except Exception as e:
            raise RuntimeError(f"road_distances 失敗：{e}") from e

        result = {}
        for i in starts:
            for k in ends:
                cost = routed.get((i, k))
                # 起訖吸附到同一個位置時 pgRouting 不回傳這一組，路上距離就是 0
                if cost is None and snaps[i][:2] == snaps[k][:2]:
                    cost = 0.0
                if cost is not None:
                    result[(i, k - len(origins))] = cost + snaps[i][2] + snaps[k][2]
        return result
