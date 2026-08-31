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

# 以下是新增的
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


class ShelterRepository:
    def __init__(self):
        self.conn_params = {
            "dbname": config.POSTGRES_DB,
            "user": config.POSTGRES_USER,
            "password": config.POSTGRES_PASSWORD,
            "host": config.POSTGRES_HOST,
            "port": config.POSTGRES_PORT
        }

    # 以下是新增的
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

    UPSERT_SQL = """
        INSERT INTO shelters (name, capacity, current_ppl, geom)
        VALUES (%s, %s, %s, ST_SetSRID(ST_Point(%s, %s), 4326))
        ON CONFLICT (name) DO UPDATE SET
            capacity = EXCLUDED.capacity,
            current_ppl = EXCLUDED.current_ppl,
            geom = EXCLUDED.geom;
    """

    def upsert_shelter(self, shelter: Shelter):
        try:
            with self._cursor() as cursor:
                cursor.execute(self.UPSERT_SQL, (
                    shelter.name,
                    shelter.capacity,
                    shelter.current_people,
                    shelter.lon,
                    shelter.lat
                ))
        except Exception as e:
            raise RuntimeError(f"upsert_shelter 失敗：{e}")

    # 以下是新增的
    def upsert_shelters(self, shelters: list[Shelter]) -> int:
        if not shelters:
            return 0
        params = [
            (s.name, s.capacity, s.current_people, s.lon, s.lat)
            for s in shelters
        ]
        try:
            with self._cursor() as cursor:
                cursor.executemany(self.UPSERT_SQL, params)
        except Exception as e:
            raise RuntimeError(f"upsert_shelters 失敗：{e}")
        return len(params)

    def get_all_shelters(self):
        shelters = []
        try:
            with self._cursor() as cursor:
                cursor.execute(
                    "SELECT name, capacity, current_ppl, ST_Y(geom::geometry), ST_X(geom::geometry) FROM shelters"
                )
                rows = cursor.fetchall()
                for row in rows:
                    shelters.append(Shelter(
                        name=row[0],
                        capacity=row[1],
                        current_people=row[2],
                        lat=row[3],
                        lon=row[4]
                    ))
        except Exception as e:
            raise RuntimeError(f"get_all_shelters error：{e}")
        return shelters

    def get_shelters_in_radius(self, lat: float, lon: float, radius_km: float):
        """
        使用 PostGIS 找出中心點半徑內的避難所
        """
        impacted_shelters = []
        try:
            with self._cursor() as cursor:
                sql = """
                    SELECT name, capacity, current_ppl, ST_Y(geom::geometry), ST_X(geom::geometry)
                    FROM shelters
                    WHERE ST_DWithin(
                        geom,
                        ST_SetSRID(ST_Point(%s, %s), 4326)::geography,
                        %s
                    );
                """
                cursor.execute(sql, (lon, lat, radius_km * 1000))
                rows = cursor.fetchall()
                for row in rows:
                    impacted_shelters.append({
                        "name": row[0],
                        "capacity": row[1],
                        "current_ppl": row[2],
                        "remaining": max(0, row[1] - row[2]),
                        "lat": row[3],
                        "lon": row[4]
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
                sql = """
                    SELECT
                        name,
                        capacity,
                        current_ppl,
                        ST_Y(geom::geometry) AS lat,
                        ST_X(geom::geometry) AS lon,
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
                rows = cursor.fetchall()
                for row in rows:
                    remaining = max(0, row[1] - row[2])
                    nearest.append({
                        "name": row[0],
                        "capacity": row[1],
                        "current_ppl": row[2],
                        "remaining": remaining,
                        "lat": float(row[3]),
                        "lon": float(row[4]),
                        "distance_km": float(row[5])
                    })
        except Exception as e:
            raise RuntimeError(f"get_nearest_shelters 失敗：{e}")
        return nearest
