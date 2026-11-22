import logging
import threading
import time
from contextlib import contextmanager

from psycopg2 import OperationalError
from psycopg2.pool import PoolError, ThreadedConnectionPool

import config

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


class Database:
    """各類查詢共用的連線設定與 cursor：一個 with 區塊是一個交易，成功 commit、失敗 rollback"""

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
