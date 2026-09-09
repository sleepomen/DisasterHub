from unittest.mock import MagicMock, patch

import pytest
from psycopg2.pool import PoolError

from repositories import shelter_repository
from repositories.shelter_repository import ShelterRepository


def fake_pool(getconn_results):
    pool = MagicMock()
    conn = MagicMock()
    conn.closed = False
    pool.getconn.side_effect = [r if r is not None else conn for r in getconn_results]
    return pool, conn


def test_cursor_waits_for_a_free_connection_when_pool_is_exhausted():
    # 同時進來的請求超過池子大小時，等一下再拿，而不是立刻 500
    pool, conn = fake_pool([PoolError("exhausted"), PoolError("exhausted"), None])
    with patch.object(shelter_repository, "_get_pool", return_value=pool), \
         patch.object(shelter_repository, "POOL_WAIT_SECONDS", 1.0), \
         patch.object(shelter_repository, "POOL_RETRY_INTERVAL", 0.001):
        ShelterRepository().ping()
    assert pool.getconn.call_count == 3
    conn.commit.assert_called_once()
    pool.putconn.assert_called_once_with(conn, close=False)


def test_cursor_gives_up_after_wait_deadline():
    pool, _ = fake_pool([PoolError("exhausted")] * 1000)
    with patch.object(shelter_repository, "_get_pool", return_value=pool), \
         patch.object(shelter_repository, "POOL_WAIT_SECONDS", 0.02), \
         patch.object(shelter_repository, "POOL_RETRY_INTERVAL", 0.001):
        with pytest.raises(RuntimeError) as exc:
            ShelterRepository().ping()
    assert "連線池已滿" in str(exc.value)
    pool.putconn.assert_not_called()


def test_sync_upsert_never_overwrites_occupancy():
    # 模擬回寫的 current_ppl 不能被重啟同步洗掉，只允許在容量縮小時往下夾
    sql = ShelterRepository.UPSERT_SQL
    assert "current_ppl = EXCLUDED.current_ppl" not in sql
    assert "LEAST(shelters.current_ppl, EXCLUDED.capacity)" in sql


def test_set_occupancy_clamps_between_zero_and_capacity():
    sql = ShelterRepository.SET_OCCUPANCY_SQL
    assert "LEAST(GREATEST(v.ppl, 0), s.capacity)" in sql


def test_set_occupancy_with_nothing_skips_database():
    # 空字典不該去碰連線池（測試環境沒有資料庫）
    assert ShelterRepository().set_occupancy({}) == 0
