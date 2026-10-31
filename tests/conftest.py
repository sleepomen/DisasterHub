import pytest

from repositories import shelter_repository
from repositories.shelter_repository import ShelterRepository


@pytest.fixture
def real_repo(monkeypatch):
    """連真的資料庫；結束時關掉這裡建立的連線池，其他測試看到的全域連線池仍是空的"""
    monkeypatch.setattr(shelter_repository, "_pool", None)
    yield ShelterRepository()
    if shelter_repository._pool is not None:
        shelter_repository._pool.closeall()
