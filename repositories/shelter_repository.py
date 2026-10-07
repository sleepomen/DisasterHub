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


SELECT_COLUMNS = "name, capacity, current_ppl, ST_Y(geom::geometry), ST_X(geom::geometry), COALESCE(address, '')"


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
        except Exception as e:
            raise RuntimeError(f"ensure_schema 失敗：{e}") from e

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
