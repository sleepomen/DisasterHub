import pytest

from repositories import db
from repositories.shelter_repository import ShelterRepository


@pytest.fixture
def real_repo(monkeypatch):
    """連真的資料庫；結束時關掉這裡建立的連線池，其他測試看到的全域連線池仍是空的"""
    monkeypatch.setattr(db, "_pool", None)
    yield ShelterRepository()
    if db._pool is not None:
        db._pool.closeall()
