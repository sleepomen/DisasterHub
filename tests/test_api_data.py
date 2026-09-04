from unittest.mock import patch
import pytest
from models.shelter import Shelter

FAKE_SHELTERS = [
    Shelter(name="[HUALIEN] 甲", capacity=100, lat=23.9, lon=121.6, current_people=10),
    Shelter(name="[YILAN] 乙", capacity=300, lat=24.7, lon=121.7, current_people=0),
]


class FakeEmbedding:
    def __call__(self, input):
        return [[0.0, 0.0, 1.0] for _ in input]

    def name(self):
        return "fake"

    def embed_query(self, input):
        return self(input)

    def embed_documents(self, input):
        return self(input)


@pytest.fixture
def client():
    with patch("repositories.shelter_repository.ShelterRepository.get_all_shelters", return_value=FAKE_SHELTERS), \
         patch("repositories.shelter_repository.ShelterRepository.get_shelters_in_radius", return_value=[]), \
         patch("repositories.shelter_repository.ShelterRepository.ensure_schema"), \
         patch("services.vector_store.build_embedding_function", return_value=FakeEmbedding()), \
         patch("services.vector_store.VectorStore.build_index"), \
         patch("services.sync_service.DataSyncService.sync"):
        from fastapi.testclient import TestClient
        import app as app_module
        with TestClient(app_module.app) as c:
            yield c


def test_shelters_endpoint(client):
    res = client.get("/api/shelters")
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


def test_health_is_liveness_only(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_ready_fails_when_database_down(client):
    with patch("repositories.shelter_repository.ShelterRepository.ping", side_effect=RuntimeError("boom")), \
         patch("services.health.check_ollama", return_value=(True, "ok")):
        res = client.get("/health/ready")
    assert res.status_code == 503
    body = res.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"]["ok"] is False


def test_ready_fails_when_index_empty(client):
    with patch("repositories.shelter_repository.ShelterRepository.ping"), \
         patch("services.vector_store.VectorStore.count", return_value=0), \
         patch("services.health.check_ollama", return_value=(True, "ok")):
        res = client.get("/health/ready")
    assert res.status_code == 503
    assert res.json()["checks"]["vector_index"]["ok"] is False


def test_ready_ignores_ollama_being_down(client):
    with patch("repositories.shelter_repository.ShelterRepository.ping"), \
         patch("services.vector_store.VectorStore.count", return_value=2), \
         patch("services.health.check_ollama", return_value=(False, "unreachable (ConnectionError)")):
        res = client.get("/health/ready")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ready"
    assert body["checks"]["ollama"] == {"ok": False, "required": False, "detail": "unreachable (ConnectionError)"}


def test_occupancy_rejects_negative_and_empty(client):
    assert client.post("/api/occupancy", json={"occupancy": []}).status_code == 422
    assert client.post("/api/occupancy", json={"occupancy": [{"name": "x", "current_ppl": -1}]}).status_code == 422


def test_occupancy_writes_back_and_reindexes_changed_only(client):
    after = [
        Shelter(name="[HUALIEN] 甲", capacity=100, lat=23.9, lon=121.6, current_people=90),
        Shelter(name="[YILAN] 乙", capacity=300, lat=24.7, lon=121.7, current_people=0),
    ]
    with patch("repositories.shelter_repository.ShelterRepository.get_all_shelters", side_effect=[FAKE_SHELTERS, after]),          patch("repositories.shelter_repository.ShelterRepository.set_occupancy", return_value=1) as set_occ,          patch("services.vector_store.VectorStore.upsert_shelters", return_value=1) as upsert:
        res = client.post("/api/occupancy", json={"occupancy": [{"name": "[HUALIEN] 甲", "current_ppl": 90}]})
    assert res.status_code == 200
    body = res.json()
    assert body["updated"] == 1
    assert body["reindexed"] == 1
    assert body["occupancy"] == {"[HUALIEN] 甲": 90, "[YILAN] 乙": 0}
    set_occ.assert_called_once_with({"[HUALIEN] 甲": 90})
    assert [s.name for s in upsert.call_args.args[0]] == ["[HUALIEN] 甲"]


def test_occupancy_reports_database_failure(client):
    with patch("repositories.shelter_repository.ShelterRepository.set_occupancy", side_effect=RuntimeError("db down")):
        res = client.post("/api/occupancy", json={"occupancy": [{"name": "[HUALIEN] 甲", "current_ppl": 9}]})
    assert res.status_code == 500
    assert "db down" not in res.text


def test_reset_restores_baseline_occupancy(client):
    baseline = {"[HUALIEN] 甲": 0, "[YILAN] 乙": 0}
    with patch("services.sync_service.DataSyncService.baseline_occupancy", return_value=baseline),          patch("repositories.shelter_repository.ShelterRepository.set_occupancy", return_value=2) as set_occ,          patch("services.vector_store.VectorStore.upsert_shelters", return_value=0):
        res = client.post("/api/reset_simulation")
    assert res.status_code == 200
    assert res.json()["status"] == "success"
    assert "occupancy" in res.json()
    set_occ.assert_called_once_with(baseline)


def test_population_endpoint(client):
    res = client.get("/api/population")
    assert res.status_code == 200
    body = res.json()
    assert len(body["townships"]) == 29
    assert {"name", "county", "lat", "lon", "pop"} <= set(body["townships"][0].keys())
    assert len(body["coastline"]) > 10


def test_simulate_includes_population_estimate(client):
    impacted = [{"name": "[HUALIEN] 甲", "capacity": 100, "current_ppl": 10, "remaining": 90,
                 "lat": 23.9, "lon": 121.6, "address": ""}]
    with patch("repositories.shelter_repository.ShelterRepository.get_shelters_in_radius", return_value=impacted):
        res = client.post("/api/simulate_disaster", json={"lat": 23.977, "lon": 121.601, "radius": 10, "type": "earthquake"})
    assert res.status_code == 200
    pop = res.json()["population"]
    assert pop["covered_population"] > 150000
    assert pop["total_remaining"] == 90
    assert pop["placeable"] == 90
    assert pop["shortfall"] == pop["estimated_evacuees"] - 90
    assert pop["townships"][0]["name"] == "花蓮市"

    # 聊天服務拿到的是同一份估算，AI 才會講同樣的數字
    import app as app_module
    assert app_module.chat_service.latest_simulation["population"] == pop
