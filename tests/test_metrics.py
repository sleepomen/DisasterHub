import threading

from services.metrics import Metrics, _percentile


def test_counters_start_at_zero_only_when_registered():
    m = Metrics()
    # 沒登記過的指標不會出現，「從未發生」和「沒有這個指標」才分得開
    assert m.snapshot()["counters"] == {}
    m.register("a.b", "c.d")
    assert m.snapshot()["counters"] == {"a.b": 0, "c.d": 0}
    m.incr("a.b")
    m.incr("a.b", 3)
    assert m.snapshot()["counters"] == {"a.b": 4, "c.d": 0}


def test_incr_accepts_unregistered_names():
    m = Metrics()
    m.incr("ad.hoc")
    assert m.snapshot()["counters"]["ad.hoc"] == 1


def test_key_count_is_capped():
    # 指標名稱都是寫死的常數，但萬一有人把使用者輸入當名稱，記憶體不能跟著長
    m = Metrics(max_keys=3)
    for i in range(10):
        m.incr(f"k{i}")
        m.observe(f"s{i}", 1.0)
    assert len(m.snapshot()["counters"]) == 3
    assert len(m.snapshot()["latency_ms"]) == 3


def test_latency_percentiles_and_sample_cap():
    m = Metrics(sample_size=100)
    for value in range(1, 201):
        m.observe("chat.generated", value)
    stats = m.snapshot()["latency_ms"]["chat.generated"]
    # 只留最後 100 筆（101..200），所以百分位是「最近的請求」而不是開機以來全部
    assert stats["count"] == 100
    assert stats["p50"] == 150.0
    assert stats["p95"] == 195.0
    assert stats["max"] == 200.0


def test_percentile_uses_nearest_rank():
    assert _percentile([10.0], 0.95) == 10.0
    assert _percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.0
    assert _percentile([1.0, 2.0, 3.0, 4.0], 0.95) == 4.0


def test_route_is_counted_and_remembered_per_thread():
    m = Metrics()
    m.route("rag")
    assert m.last_route() == "rag"
    assert m.snapshot()["counters"]["chat.route.rag"] == 1

    seen = []
    # 另一條執行緒有自己的 last_route，不會被別人的請求汙染
    t = threading.Thread(target=lambda: seen.append(m.last_route()))
    t.start()
    t.join()
    assert seen == ["-"]


def test_snapshot_has_uptime_and_is_a_copy():
    m = Metrics()
    m.register("x")
    snap = m.snapshot()
    assert snap["uptime_s"] >= 0
    snap["counters"]["x"] = 999
    assert m.snapshot()["counters"]["x"] == 0


def test_reset_zeroes_counters_but_keeps_registered_names():
    m = Metrics()
    m.register("a")
    m.incr("a")
    m.incr("b")
    m.observe("s", 5)
    m.reset()
    snap = m.snapshot()
    assert snap["counters"] == {"a": 0, "b": 0}
    assert snap["latency_ms"] == {}


def test_concurrent_incr_does_not_lose_counts():
    m = Metrics()
    m.register("hits")

    def work():
        for _ in range(500):
            m.incr("hits")

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert m.snapshot()["counters"]["hits"] == 2000
