import time
import logging
import threading
from contextlib import contextmanager
from psycopg2 import OperationalError
from psycopg2.pool import ThreadedConnectionPool
from models.shelter import Shelter
import config

MAX_RETRIES = 3
RETRY_DELAY = 2  # 秒

logger = logging.getLogger(__name__)
_pool = None
_pool_lock = threading.Lock()


def _get_pool(conn_params):
    global _pool
    with _pool_lock:
        if _pool is None:
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    _pool = ThreadedConnectionPool(config.DB_POOL_MIN, config.DB_POOL_MAX, **conn_params)
                    break
                except OperationalError as e:
                    logger.warning("DB 連線失敗（第 %d 次）：%s", attempt, e)
                    if attempt < MAX_RETRIES:
                        time.sleep(RETRY_DELAY)
                    else:
                        raise RuntimeError(f"資料庫連線失敗，已重試 {MAX_RETRIES} 次：{e}")
        return _pool


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
            "port": config.POSTGRES_PORT
        }

    @contextmanager
    def _cursor(self):
        pool = _get_pool(self.conn_params)
        conn = pool.getconn()
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

    def ensure_schema(self):
        try:
            with self._cursor() as cursor:
                cursor.execute("ALTER TABLE shelters ADD COLUMN IF NOT EXISTS address VARCHAR(200) DEFAULT ''")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_shelters_geom ON shelters USING GIST (geom)")
                cursor.execute("DROP TABLE IF EXISTS roads")
        except Exception as e:
            raise RuntimeError(f"ensure_schema 失敗：{e}")

    UPSERT_SQL = """
        INSERT INTO shelters (name, capacity, current_ppl, address, geom)
        VALUES (%s, %s, %s, %s, ST_SetSRID(ST_Point(%s, %s), 4326))
        ON CONFLICT (name) DO UPDATE SET
            capacity = EXCLUDED.capacity,
            current_ppl = EXCLUDED.current_ppl,
            address = EXCLUDED.address,
            geom = EXCLUDED.geom;
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
            raise RuntimeError(f"upsert_shelter 失敗：{e}")

    def upsert_shelters(self, shelters: list[Shelter]) -> int:
        if not shelters:
            return 0
        params = [self._upsert_params(s) for s in shelters]
        try:
            with self._cursor() as cursor:
                cursor.executemany(self.UPSERT_SQL, params)
        except Exception as e:
            raise RuntimeError(f"upsert_shelters 失敗：{e}")
        return len(params)

    def get_all_shelters(self):
        try:
            with self._cursor() as cursor:
                cursor.execute(f"SELECT {SELECT_COLUMNS} FROM shelters")
                return [_row_to_shelter(row) for row in cursor.fetchall()]
        except Exception as e:
            raise RuntimeError(f"get_all_shelters error：{e}")

    def get_shelters_in_radius(self, lat: float, lon: float, radius_km: float):
        """
        使用 PostGIS 找出中心點半徑內的避難所
        """
        impacted_shelters = []
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
                for row in cursor.fetchall():
                    impacted_shelters.append({
                        "name": row[0],
                        "capacity": row[1],
                        "current_ppl": row[2],
                        "remaining": max(0, row[1] - row[2]),
                        "lat": row[3],
                        "lon": row[4],
                        "address": row[5],
                    })
        except Exception as e:
            raise RuntimeError(f"get_shelters_in_radius 失敗：{e}")
        return impacted_shelters

    def get_nearest_shelters(self, lat: float, lon: float, limit: int = 5):
        """
        使用 PostGIS ST_Distance 找出距離中心點最近的 N 個避難所，依距離排序
        """
        nearest = []
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
                for row in cursor.fetchall():
                    nearest.append({
                        "name": row[0],
                        "capacity": row[1],
                        "current_ppl": row[2],
                        "remaining": max(0, row[1] - row[2]),
                        "lat": float(row[3]),
                        "lon": float(row[4]),
                        "address": row[5],
                        "distance_km": float(row[6]),
                    })
        except Exception as e:
            raise RuntimeError(f"get_nearest_shelters 失敗：{e}")
        return nearest
