from unittest.mock import MagicMock, patch
from models.shelter import Shelter
from services.sync_service import DataSyncService

FAKE = [
    Shelter(name="[HUALIEN] 甲", capacity=100, lat=23.9, lon=121.6),
    Shelter(name="[YILAN] 乙", capacity=300, lat=24.7, lon=121.7),
]


def _service_with(fetched):
    with patch("services.sync_service.ShelterRepository") as repo_cls, \
         patch("services.sync_service.DataFetcher") as fetcher_cls:
        fetcher_cls.return_value.get_shelters.return_value = fetched
        service = DataSyncService()
        return service, repo_cls.return_value


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


def test_sync_skips_when_no_data():
    service, repo = _service_with([])

    service.sync()

    repo.upsert_shelters.assert_not_called()
    repo.upsert_shelter.assert_not_called()


def test_row_fallback_survives_single_failure():
    service, repo = _service_with(FAKE)
    repo.upsert_shelters.side_effect = RuntimeError("batch failed")
    repo.upsert_shelter.side_effect = [RuntimeError("row failed"), None]

    service.sync()

    assert repo.upsert_shelter.call_count == 2
