# 以下是新增的
from unittest.mock import patch
import pytest
from models.shelter import Shelter

FAKE_SHELTERS = [
    Shelter(name="[HUALIEN] 甲", capacity=100, lat=23.9, lon=121.6, current_people=10),
    Shelter(name="[YILAN] 乙", capacity=300, lat=24.7, lon=121.7, current_people=0),
]


@pytest.fixture
def client():
    with patch("repositories.shelter_repository.ShelterRepository.get_all_shelters", return_value=FAKE_SHELTERS), \
         patch("repositories.shelter_repository.ShelterRepository.get_shelters_in_radius", return_value=[]), \
         patch("services.vector_store.VectorStore.build_index"), \
         patch("services.sync_service.DataSyncService.sync"):
        from fastapi.testclient import TestClient
        import app as app_module
        with TestClient(app_module.app) as c:
            yield c


def test_3d_data_endpoint(client):
    res = client.get("/api/3d_data")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["name"] == "[HUALIEN] 甲"
    assert {"name", "lat", "lon", "z", "ppl"} <= set(data[0].keys())


def test_simulate_rejects_bad_type(client):
    res = client.post("/api/simulate_disaster", json={"lat": 23.9, "lon": 121.6, "radius": 10, "type": "tsunami"})
    assert res.status_code == 422


def test_simulate_rejects_out_of_range(client):
    res = client.post("/api/simulate_disaster", json={"lat": 99, "lon": 121.6, "radius": 10})
    assert res.status_code == 422


def test_sync_requires_api_key(client):
    with patch("config.SYNC_API_KEY", "secret"):
        assert client.post("/api/sync").status_code == 401
        assert client.post("/api/sync", headers={"X-API-Key": "wrong"}).status_code == 401


def test_sync_disabled_without_key(client):
    with patch("config.SYNC_API_KEY", ""):
        assert client.post("/api/sync", headers={"X-API-Key": "x"}).status_code == 503


def test_index_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "<html" in res.text.lower()
