from unittest.mock import patch

from models.shelter import Shelter
from services.sync_service import DataSyncService

FAKE = [
    Shelter(name="[HUALIEN] 甲", capacity=100, lat=23.9, lon=121.6),
    Shelter(name="[YILAN] 乙", capacity=300, lat=24.7, lon=121.7),
]


def _service_with(fetched, failed_files=(), skipped=0):
    with (
        patch("services.sync_service.ShelterRepository") as repo_cls,
        patch("services.sync_service.DataFetcher") as fetcher_cls,
    ):
        fetcher = fetcher_cls.return_value
        fetcher.get_shelters.return_value = fetched
        # MagicMock 的屬性預設是 truthy，不明確設成「乾淨」的話清理步驟會永遠被略過，
        # 測試就變成什麼都沒驗
        fetcher.failed_files = list(failed_files)
        fetcher.skipped = skipped
        repo = repo_cls.return_value
        repo.delete_missing.return_value = 0
        return DataSyncService(), repo


def test_sync_uses_batch_upsert():
    service, repo = _service_with(FAKE)
    repo.upsert_shelters.return_value = len(FAKE)

    service.sync()

    repo.upsert_shelters.assert_called_once_with(FAKE)
    repo.upsert_shelter.assert_not_called()


def test_sync_falls_back_to_row_upsert_when_batch_fails():
    service, repo = _service_with(FAKE)
    repo.upsert_shelters.side_effect = RuntimeError("batch failed")

    service.sync()

    assert repo.upsert_shelter.call_count == len(FAKE)


def test_sync_raises_when_no_data():
    # 沒讀到資料不能當成同步成功，啟動流程與 /api/sync 要看得到失敗
    import pytest
    service, repo = _service_with([])

    with pytest.raises(RuntimeError):
        service.sync()

    repo.upsert_shelters.assert_not_called()
    repo.upsert_shelter.assert_not_called()


def test_sync_raises_when_nothing_could_be_written():
    import pytest
    service, repo = _service_with(FAKE)
    repo.upsert_shelters.side_effect = RuntimeError("batch failed")
    repo.upsert_shelter.side_effect = RuntimeError("row failed")

    with pytest.raises(RuntimeError):
        service.sync()


def test_sync_returns_written_count():
    service, repo = _service_with(FAKE)
    repo.upsert_shelters.return_value = len(FAKE)
    assert service.sync() == len(FAKE)


def test_row_fallback_survives_single_failure():
    service, repo = _service_with(FAKE)
    repo.upsert_shelters.side_effect = RuntimeError("batch failed")
    repo.upsert_shelter.side_effect = [RuntimeError("row failed"), None]

    service.sync()

    assert repo.upsert_shelter.call_count == 2


def test_baseline_occupancy_comes_from_source_data():
    service, _ = _service_with([
        Shelter(name="[HUALIEN] 甲", capacity=100, lat=23.9, lon=121.6, current_people=7),
        Shelter(name="[YILAN] 乙", capacity=300, lat=24.7, lon=121.7),
    ])
    assert service.baseline_occupancy() == {"[HUALIEN] 甲": 7, "[YILAN] 乙": 0}


def test_sync_prunes_shelters_removed_from_the_source():
    # 從來源 JSON 刪掉一間避難所之後，資料庫那一列必須跟著消失，
    # 否則 AI 會繼續推薦一個已經不是避難所的地點；改名的話舊名新名會各留一列
    service, repo = _service_with(FAKE)
    repo.upsert_shelters.return_value = len(FAKE)
    repo.delete_missing.return_value = 1

    service.sync()

    repo.delete_missing.assert_called_once_with(["[HUALIEN] 甲", "[YILAN] 乙"])


def test_sync_refuses_to_prune_when_a_source_file_failed_to_parse():
    # 一個檔案就是一個縣。少一個檔案時來源看起來就只有兩個縣，
    # 照著清理會把整個宜蘭刪掉——這是整個清理步驟最危險的情況
    service, repo = _service_with(FAKE, failed_files=["yilan_shelter.json"])
    repo.upsert_shelters.return_value = len(FAKE)

    service.sync()

    repo.delete_missing.assert_not_called()


def test_sync_refuses_to_prune_when_rows_were_skipped():
    # 被略過的那幾筆在資料庫裡的列會被誤刪，連同模擬回寫的收容人數一起消失
    service, repo = _service_with(FAKE, skipped=1)
    repo.upsert_shelters.return_value = len(FAKE)

    service.sync()

    repo.delete_missing.assert_not_called()


def test_sync_refuses_to_prune_after_a_partial_write():
    # 沒寫進去的那幾筆會被當成「來源已移除」而刪掉
    service, repo = _service_with(FAKE)
    repo.upsert_shelters.side_effect = RuntimeError("batch failed")
    repo.upsert_shelter.side_effect = [None, RuntimeError("row failed")]

    service.sync()

    repo.delete_missing.assert_not_called()


def test_prune_count_is_recorded():
    from services.metrics import metrics
    metrics.reset()
    service, repo = _service_with(FAKE)
    repo.upsert_shelters.return_value = len(FAKE)
    repo.delete_missing.return_value = 3

    service.sync()

    counters = metrics.snapshot()["counters"]
    assert counters["sync.pruned"] == 3
    assert counters["sync.prune_skipped"] == 0

    # 不敢清理的次數也要留下來：連續出現代表來源資料壞了而沒人發現
    metrics.reset()
    service, repo = _service_with(FAKE, failed_files=["yilan_shelter.json"])
    repo.upsert_shelters.return_value = len(FAKE)
    service.sync()
    assert metrics.snapshot()["counters"]["sync.prune_skipped"] == 1
