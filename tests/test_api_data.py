import asyncio
import json
from unittest.mock import patch

import pytest

from models.shelter import Shelter

FAKE_SHELTERS = [
    Shelter(name="[HUALIEN] 甲", capacity=100, lat=23.9, lon=121.6, current_people=10),
    Shelter(name="[YILAN] 乙", capacity=300, lat=24.7, lon=121.7, current_people=0),
]

# 模擬 / 回寫 / 重置要登入，或帶腳本用的 X-API-Key；fixture 會把這些設定塞進 config
ADMIN = {"username": "ops", "password": "pw-secret"}
WRITE_KEY = "write-secret"
WRITE_HEADERS = {"X-API-Key": WRITE_KEY}
WRITE_ENDPOINTS = [
    ("/api/simulate_disaster", {"lat": 23.9, "lon": 121.6, "radius": 10, "type": "earthquake"}),
    ("/api/occupancy", {"occupancy": [{"name": "[HUALIEN] 甲", "current_ppl": 9}]}),
    ("/api/reset_simulation", None),
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
         patch("services.sync_service.DataSyncService.sync"), \
         patch("config.WRITE_API_KEY", WRITE_KEY), \
         patch("config.ADMIN_USERNAME", ADMIN["username"]), \
         patch("config.ADMIN_PASSWORD", ADMIN["password"]):
        from fastapi.testclient import TestClient

        import app as app_module
        with TestClient(app_module.app) as c:
            yield c
        # 登入節流與聊天用量都是 process 全域狀態，別讓一個測試的紀錄影響下一個：
        # 整個 session 的 /api/chat 會累加在同一個視窗裡，不清就會莫名收到 429
        app_module.login_throttle.reset("testclient")
        app_module.chat_limiter.reset("testclient")
        app_module.metrics.reset()


def login(client, **overrides):
    return client.post("/api/login", json={**ADMIN, **overrides})


def test_shelters_endpoint(client):
    res = client.get("/api/shelters")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["name"] == "[HUALIEN] 甲"
    assert {"name", "lat", "lon", "z", "ppl"} <= set(data[0].keys())


def test_simulate_rejects_bad_type(client):
    res = client.post("/api/simulate_disaster", headers=WRITE_HEADERS,
                      json={"lat": 23.9, "lon": 121.6, "radius": 10, "type": "tsunami"})
    assert res.status_code == 422


def test_simulate_rejects_out_of_range(client):
    res = client.post("/api/simulate_disaster", headers=WRITE_HEADERS, json={"lat": 99, "lon": 121.6, "radius": 10})
    assert res.status_code == 422


@pytest.mark.parametrize("path,body", WRITE_ENDPOINTS)
def test_write_endpoints_require_login_or_key(client, path, body):
    # 沒登入、沒金鑰、金鑰錯、cookie 亂改都是 401，而且不能碰到資料庫
    with patch("repositories.shelter_repository.ShelterRepository.set_occupancy") as set_occ, \
         patch("repositories.shelter_repository.ShelterRepository.get_shelters_in_radius") as in_radius:
        assert client.post(path, json=body).status_code == 401
        assert client.post(path, json=body, headers={"X-API-Key": "wrong"}).status_code == 401
        client.cookies.set("disasterhub_session", "b3Bz.9999999999.deadbeef")
        assert client.post(path, json=body).status_code == 401
    set_occ.assert_not_called()
    in_radius.assert_not_called()


@pytest.mark.parametrize("path,body", WRITE_ENDPOINTS)
def test_write_endpoints_disabled_without_any_credentials(client, path, body):
    with patch("config.WRITE_API_KEY", ""), patch("config.ADMIN_USERNAME", ""), patch("config.ADMIN_PASSWORD", ""):
        res = client.post(path, json=body, headers=WRITE_HEADERS)
    assert res.status_code == 503
    assert "ADMIN_USERNAME" in res.json()["detail"]


def test_login_sets_cookie_and_unlocks_write_endpoints(client):
    assert client.get("/api/me").json() == {"authenticated": False, "user": None, "login_enabled": True}

    res = login(client)
    assert res.status_code == 200
    assert res.json()["user"] == "ops"
    cookie = res.headers["set-cookie"].lower()
    assert "disasterhub_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "secure" not in cookie  # 測試是 http，走 https 代理時才加

    me = client.get("/api/me").json()
    assert me["authenticated"] is True and me["user"] == "ops"

    with patch("services.sync_service.DataSyncService.baseline_occupancy", return_value={}), \
         patch("repositories.shelter_repository.ShelterRepository.set_occupancy", return_value=0), \
         patch("services.vector_store.VectorStore.upsert_shelters", return_value=0):
        assert client.post("/api/reset_simulation").status_code == 200


def test_login_marks_cookie_secure_behind_https_proxy(client):
    res = login(client)
    assert "secure" not in res.headers["set-cookie"].lower()
    # 直接對外時不信任 X-Forwarded-Proto；只有明確設了 TRUST_PROXY_HEADERS 才讀
    res = client.post("/api/login", json=ADMIN, headers={"X-Forwarded-Proto": "https"})
    assert "secure" not in res.headers["set-cookie"].lower()
    with patch("config.TRUST_PROXY_HEADERS", True):
        res = client.post("/api/login", json=ADMIN, headers={"X-Forwarded-Proto": "https"})
    assert "secure" in res.headers["set-cookie"].lower()


def test_forwarded_for_only_trusted_behind_proxy():
    from starlette.requests import Request

    import app as app_module
    scope = {
        "type": "http", "method": "POST", "path": "/api/login", "query_string": b"",
        "headers": [(b"x-forwarded-for", b"9.9.9.9, 10.0.0.1")],
        "client": ("1.2.3.4", 1234),
    }
    request = Request(scope)
    with patch("config.TRUST_PROXY_HEADERS", False):
        assert app_module.client_key(request) == "1.2.3.4"
    with patch("config.TRUST_PROXY_HEADERS", True):
        assert app_module.client_key(request) == "9.9.9.9"


def test_login_failure_log_does_not_contain_username(client, caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="app"):
        assert login(client, username="hunter2-typed-in-wrong-box", password="nope").status_code == 401
    assert "登入失敗" in caplog.text
    assert "hunter2" not in caplog.text


def test_login_rejects_wrong_credentials(client):
    assert login(client, password="nope").status_code == 401
    assert login(client, username="admin").status_code == 401
    assert client.get("/api/me").json()["authenticated"] is False
    assert "set-cookie" not in login(client, password="nope").headers


def test_login_disabled_without_credentials(client):
    with patch("config.ADMIN_USERNAME", ""), patch("config.ADMIN_PASSWORD", ""):
        assert login(client).status_code == 503
        assert client.get("/api/me").json()["login_enabled"] is False


def test_login_throttles_after_repeated_failures(client):
    for _ in range(5):
        assert login(client, password="nope").status_code == 401
    res = login(client)  # 密碼對了也要等冷卻
    assert res.status_code == 429
    assert "秒後再試" in res.json()["detail"]


def test_logout_clears_session(client):
    login(client)
    assert client.get("/api/me").json()["authenticated"] is True
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/me").json()["authenticated"] is False
    assert client.post("/api/reset_simulation").status_code == 401


def test_expired_session_is_rejected(client):
    import app as app_module
    login(client)
    with patch.object(app_module.session_manager, "verify", return_value=None):
        assert client.get("/api/me").json()["authenticated"] is False
        assert client.post("/api/reset_simulation").status_code == 401


def test_sync_key_does_not_unlock_write_endpoints(client):
    with patch("config.SYNC_API_KEY", "sync-secret"):
        res = client.post("/api/reset_simulation", headers={"X-API-Key": "sync-secret"})
    assert res.status_code == 401


def test_read_endpoints_stay_open_without_key(client):
    assert client.get("/api/shelters").status_code == 200
    assert client.get("/api/population").status_code == 200


def test_sync_requires_api_key(client):
    with patch("config.SYNC_API_KEY", "secret"):
        assert client.post("/api/sync").status_code == 401
        assert client.post("/api/sync", headers={"X-API-Key": "wrong"}).status_code == 401


def test_sync_accepts_write_credentials_as_recovery_path(client):
    # 啟動同步失敗時索引是空的、readiness 一直 503；沒設 SYNC_API_KEY 的部署
    # 也要能讓操作員自己把資料同步回來，而不是只剩重啟容器
    with patch("config.SYNC_API_KEY", ""), patch("app.sync_and_reindex", return_value=2) as sync:
        assert client.post("/api/sync", headers={"X-API-Key": "x"}).status_code == 401
        assert client.post("/api/sync", headers=WRITE_HEADERS).status_code == 200
        login(client)
        assert client.post("/api/sync").status_code == 200
    assert sync.call_count == 2


def test_sync_disabled_when_nothing_is_configured(client):
    with (
        patch("config.SYNC_API_KEY", ""),
        patch("config.WRITE_API_KEY", ""),
        patch("config.ADMIN_USERNAME", ""),
        patch("config.ADMIN_PASSWORD", ""),
    ):
        res = client.post("/api/sync", headers={"X-API-Key": "x"})
    assert res.status_code == 503
    assert "SYNC_API_KEY" in res.json()["detail"]


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
    assert client.post("/api/occupancy", headers=WRITE_HEADERS, json={"occupancy": []}).status_code == 422
    assert client.post("/api/occupancy", headers=WRITE_HEADERS,
                       json={"occupancy": [{"name": "x", "current_ppl": -1}]}).status_code == 422


def test_occupancy_writes_back_and_reindexes_changed_only(client):
    after = [
        Shelter(name="[HUALIEN] 甲", capacity=100, lat=23.9, lon=121.6, current_people=90),
        Shelter(name="[YILAN] 乙", capacity=300, lat=24.7, lon=121.7, current_people=0),
    ]
    with (
        patch(
            "repositories.shelter_repository.ShelterRepository.get_all_shelters",
            side_effect=[FAKE_SHELTERS, after],
        ),
        patch(
            "repositories.shelter_repository.ShelterRepository.set_occupancy", return_value=1
        ) as set_occ,
        patch("services.vector_store.VectorStore.upsert_shelters", return_value=1) as upsert,
    ):
        res = client.post("/api/occupancy", headers=WRITE_HEADERS,
                          json={"occupancy": [{"name": "[HUALIEN] 甲", "current_ppl": 90}]})
    assert res.status_code == 200
    body = res.json()
    assert body["updated"] == 1
    assert body["reindexed"] == 1
    assert body["occupancy"] == {"[HUALIEN] 甲": 90, "[YILAN] 乙": 0}
    set_occ.assert_called_once_with({"[HUALIEN] 甲": 90})
    assert [s.name for s in upsert.call_args.args[0]] == ["[HUALIEN] 甲"]


def test_occupancy_reports_database_failure(client):
    with patch("repositories.shelter_repository.ShelterRepository.set_occupancy", side_effect=RuntimeError("db down")):
        res = client.post("/api/occupancy", headers=WRITE_HEADERS,
                          json={"occupancy": [{"name": "[HUALIEN] 甲", "current_ppl": 9}]})
    assert res.status_code == 500
    assert "db down" not in res.text


def test_reset_restores_baseline_occupancy(client):
    baseline = {"[HUALIEN] 甲": 0, "[YILAN] 乙": 0}
    with (
        patch("services.sync_service.DataSyncService.baseline_occupancy", return_value=baseline),
        patch(
            "repositories.shelter_repository.ShelterRepository.set_occupancy", return_value=2
        ) as set_occ,
        patch("services.vector_store.VectorStore.upsert_shelters", return_value=0),
    ):
        res = client.post("/api/reset_simulation", headers=WRITE_HEADERS)
    assert res.status_code == 200
    assert res.json()["status"] == "success"
    assert "occupancy" in res.json()
    set_occ.assert_called_once_with(baseline)


def test_reset_keeps_simulation_snapshot_when_database_fails(client):
    # 先寫資料庫、成功才清快照；資料庫失敗時聊天不能說「尚未模擬」而地圖與資料庫卻仍是滿載
    import app as app_module
    app_module.chat_service.set_simulation({"type": "flood", "radius_km": 5, "impacted_count": 0, "impacted_shelters": []})
    with patch("services.sync_service.DataSyncService.baseline_occupancy", return_value={"[HUALIEN] 甲": 0}), \
         patch("repositories.shelter_repository.ShelterRepository.set_occupancy", side_effect=RuntimeError("db down")):
        res = client.post("/api/reset_simulation", headers=WRITE_HEADERS)
    assert res.status_code == 500
    assert app_module.chat_service.latest_simulation != {}
    app_module.chat_service.clear_simulation()


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
        res = client.post("/api/simulate_disaster", headers=WRITE_HEADERS,
                          json={"lat": 23.977, "lon": 121.601, "radius": 10, "type": "earthquake"})
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


def sse_frames(res):
    return [json.loads(line[len("data:"):]) for line in res.text.splitlines() if line.startswith("data:")]


def fake_chat_stream(_message):
    yield {"type": "delta", "text": "甲避難所"}
    yield {"type": "delta", "text": "：剩餘 90 人"}


def test_chat_streams_sse_events(client):
    with patch("app.chat_service.chat_stream", side_effect=fake_chat_stream):
        res = client.post("/api/chat", json={"message": "宜蘭有哪些避難所"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    # 反向代理把回應整段緩衝起來的話串流就沒意義了
    assert res.headers["x-accel-buffering"] == "no"
    assert sse_frames(res) == [
        {"type": "delta", "text": "甲避難所"},
        {"type": "delta", "text": "：剩餘 90 人"},
        {"type": "done"},
    ]


def test_chat_returns_429_when_all_slots_are_busy(client):
    import app as app_module
    with patch("app.chat_service.chat_stream", side_effect=fake_chat_stream) as chat:
        assert client.post("/api/chat", json={"message": "宜蘭有哪些避難所"}).status_code == 200
        # 把所有生成名額佔住：下一個問題要立刻被拒絕，而不是排隊或碰到 Ollama
        held = 0
        while app_module.chat_slots.acquire(blocking=False):
            held += 1
        try:
            res = client.post("/api/chat", json={"message": "宜蘭有哪些避難所"})
        finally:
            for _ in range(held):
                app_module.chat_slots.release()
    assert held == app_module.config.CHAT_MAX_CONCURRENT
    assert res.status_code == 429
    assert "稍後再試" in res.json()["detail"]
    assert chat.call_count == 1
    # 名額釋放後恢復正常；串流結束就要把名額還回去，不然問幾題之後整個聊天就永久 429
    with patch("app.chat_service.chat_stream", side_effect=fake_chat_stream):
        assert client.post("/api/chat", json={"message": "宜蘭有哪些避難所"}).status_code == 200


def test_chat_slot_is_released_when_generation_raises(client):
    # TestClient 會把伺服器端的例外原樣丟回來；重點是名額要還回去
    import app as app_module
    before = app_module.chat_slots._value
    with patch("app.chat_service.chat_stream", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            client.post("/api/chat", json={"message": "x"})
    assert app_module.chat_slots._value == before


def test_chat_is_rate_limited_per_source(client):
    import app as app_module
    # /api/chat 不需要登入（災時任何人都要問得到），所以用量上限是唯一擋得住
    # 「拿它當免費 LLM 無限打」的東西
    with patch("app.chat_service.chat_stream", side_effect=fake_chat_stream) as chat, \
         patch.object(app_module.chat_limiter, "max_requests", 2):
        assert client.post("/api/chat", json={"message": "x"}).status_code == 200
        assert client.post("/api/chat", json={"message": "x"}).status_code == 200
        res = client.post("/api/chat", json={"message": "x"})
    assert res.status_code == 429
    assert "太頻繁" in res.json()["detail"]
    assert int(res.headers["retry-after"]) > 0
    # 被限流的請求不該碰到生成，也不該佔用生成名額
    assert chat.call_count == 2
    assert app_module.chat_slots._value == app_module.config.CHAT_MAX_CONCURRENT


def test_chat_rate_limit_can_be_disabled(client):
    import app as app_module
    with patch("app.chat_service.chat_stream", side_effect=fake_chat_stream), \
         patch.object(app_module.chat_limiter, "max_requests", 0):
        codes = [client.post("/api/chat", json={"message": "x"}).status_code for _ in range(5)]
    assert codes == [200] * 5


def test_startup_sync_retries_in_the_background_until_it_succeeds():
    # 啟動同步失敗的常見原因（資料庫還沒起來、Ollama 還在載模型）都是暫時的，
    # 但索引空著 readiness 就一直 503，所以要自己重試而不是等人來呼叫 /api/sync
    import app as app_module
    calls = []

    def flaky(force=False):
        calls.append(force)
        if len(calls) < 3:
            raise RuntimeError("db not ready")
        return 7

    with patch("app.STARTUP_RETRY_DELAYS", (0, 0, 0, 0)), patch("app.sync_and_reindex", flaky):
        asyncio.run(app_module.retry_startup_sync())
    # 重試不帶 force：只有手動同步才強制重建整個索引
    assert calls == [False, False, False]


def test_startup_sync_retry_gives_up_after_the_last_delay(caplog):
    import logging

    import app as app_module
    with patch("app.STARTUP_RETRY_DELAYS", (0, 0)), \
         patch("app.sync_and_reindex", side_effect=RuntimeError("down")) as sync:
        with caplog.at_level(logging.ERROR, logger="app"):
            asyncio.run(app_module.retry_startup_sync())
    assert sync.call_count == 2
    assert "/api/sync" in caplog.text


def test_stats_requires_the_same_access_as_writes(client):
    # 計數器會揭露流量模式與節流狀態，不該像 /health 那樣對外公開
    assert client.get("/api/stats").status_code == 401
    res = client.get("/api/stats", headers=WRITE_HEADERS)
    assert res.status_code == 200
    body = res.json()
    assert "uptime_s" in body and "counters" in body and "latency_ms" in body
    # 預先登記的名稱即使還沒發生過也要在，否則分不出「沒發生」和「沒這個指標」
    assert body["counters"]["chat.route.rag"] == 0
    assert body["counters"]["retrieval.fell_back"] == 0


def test_rejections_are_counted_by_reason(client):
    import app as app_module
    with patch("app.chat_service.chat_stream", side_effect=fake_chat_stream), \
         patch.object(app_module.chat_limiter, "max_requests", 1):
        assert client.post("/api/chat", json={"message": "x"}).status_code == 200
        assert client.post("/api/chat", json={"message": "x"}).status_code == 429
    held = 0
    while app_module.chat_slots.acquire(blocking=False):
        held += 1
    try:
        app_module.chat_limiter.reset("testclient")
        with patch("app.chat_service.chat_stream", side_effect=fake_chat_stream):
            assert client.post("/api/chat", json={"message": "x"}).status_code == 429
    finally:
        for _ in range(held):
            app_module.chat_slots.release()
    counters = client.get("/api/stats", headers=WRITE_HEADERS).json()["counters"]
    # 被刷 vs 名額不夠是兩種完全不同的問題，混在同一個 429 裡看不出來
    assert counters["chat.rejected.rate_limit"] == 1
    assert counters["chat.rejected.busy"] == 1


def test_every_response_carries_a_request_id(client):
    res = client.get("/health")
    rid = res.headers["x-request-id"]
    assert len(rid) == 8 and rid.isalnum()
    # 每個請求都是新的 id
    assert client.get("/health").headers["x-request-id"] != rid


def test_incoming_request_id_only_trusted_behind_proxy(client):
    headers = {"X-Request-ID": "from-proxy-123"}
    assert client.get("/health", headers=headers).headers["x-request-id"] != "from-proxy-123"
    with patch("config.TRUST_PROXY_HEADERS", True):
        assert client.get("/health", headers=headers).headers["x-request-id"] == "from-proxy-123"
        # 這個值會進 log，格式不對就不能沿用（否則可以把換行塞進日誌）
        bad = {"X-Request-ID": "bad id\nINJECTED"}
        assert client.get("/health", headers=bad).headers["x-request-id"] != "bad id\nINJECTED"


def test_request_id_filter_fills_the_log_field():
    import logging

    import app as app_module

    def record():
        return logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)

    log_filter = app_module.RequestIdFilter()
    token = app_module.request_id_var.set("abc12345")
    try:
        inside = record()
        assert log_filter.filter(inside) is True
        assert inside.request_id == "abc12345"
    finally:
        app_module.request_id_var.reset(token)

    # 沒有請求情境時也要有值，否則 formatter 會因為缺欄位而炸掉
    outside = record()
    log_filter.filter(outside)
    assert outside.request_id == "-"

    # filter 要掛在 handler 上而不是 logger 上，子 logger 傳上來的紀錄才會被補欄位
    assert any(
        isinstance(flt, app_module.RequestIdFilter)
        for handler in logging.getLogger().handlers
        for flt in handler.filters
    )
