from repositories.shelter_repository import ShelterRepository


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
