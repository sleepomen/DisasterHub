import threading
from unittest.mock import patch
import pytest
from models.shelter import Shelter
from services.vector_store import VectorStore, Hit

KEYWORDS = ["宜蘭", "花蓮", "台東", "國小", "國中", "體育館", "圖書館", "公所"]


class KeywordEmbedding:
    def __call__(self, input):
        vectors = []
        for text in input:
            vec = [float(text.count(k)) for k in KEYWORDS]
            vec.append(1e-3)
            vectors.append(vec)
        return vectors

    def name(self):
        return "keyword-test"

    def embed_query(self, input):
        return self(input)

    def embed_documents(self, input):
        return self(input)


SHELTERS = [
    Shelter("[YILAN] 宜蘭國小", 500, 24.7, 121.7, 0, "宜蘭縣宜蘭市崇聖街2號"),
    Shelter("[YILAN] 羅東鎮立體育館", 800, 24.6, 121.7, 100, "宜蘭縣羅東鎮體育路15號"),
    Shelter("[HUALIEN] 中正國小", 400, 23.9, 121.6, 0, "花蓮縣花蓮市中正路210號"),
    Shelter("[TAITUNG] 台東縣立體育館", 2000, 22.7, 121.1, 0, "台東縣台東市桂林北路124號"),
]


@pytest.fixture
def store():
    s = VectorStore(embedding_function=KeywordEmbedding(), collection_name="test_vector_store")
    s.build_index(SHELTERS)
    return s


def test_retrieve_returns_hits_sorted_by_distance(store):
    hits = store.retrieve("宜蘭 國小", n_results=4)
    assert len(hits) == 4
    assert all(isinstance(h, Hit) for h in hits)
    assert hits[0].name == "[YILAN] 宜蘭國小"
    assert [h.distance for h in hits] == sorted(h.distance for h in hits)


def test_retrieve_keyword_match_prefers_matching_region(store):
    hits = store.retrieve("台東 體育館", n_results=2)
    assert hits[0].name == "[TAITUNG] 台東縣立體育館"


def test_metadata_fields(store):
    hit = store.retrieve("羅東鎮立體育館", n_results=1)[0]
    meta = hit.metadata
    assert meta["region"] == "宜蘭"
    assert meta["county"] == "宜蘭縣"
    assert meta["township"] == "羅東鎮"
    assert meta["facility"] == "體育場館"
    assert meta["size_class"] == "中型"
    assert meta["address"] == "宜蘭縣羅東鎮體育路15號"
    assert meta["capacity"] == 800
    assert meta["remaining"] == 700
    assert meta["occupancy_rate"] == 12.5


def test_document_is_enriched(store):
    hit = store.retrieve("中正國小", n_results=1)[0]
    doc = hit.document
    assert "HUALIEN" not in doc
    assert doc.startswith("中正國小（別名：中正國民小學）是花蓮地區的避難收容場所")
    assert "花蓮縣花蓮市" in doc
    assert "花蓮縣花蓮市中正路210號" in doc
    assert "設施類型為國小" in doc
    assert "容量 400 人，屬於中型避難所" in doc
    assert "尚有 400 個空位" in doc


def test_document_without_address(store):
    doc = VectorStore.build_document(Shelter("[YILAN] 宜蘭國小", 500, 24.7, 121.7, 0))
    assert "地址" not in doc
    assert "位於宜蘭縣。" in doc


def test_search_returns_joined_documents(store):
    text = store.search("宜蘭 國小", n_results=2)
    assert text.startswith("- ")
    assert text.count("\n") == 1


def test_search_strips_aliases_from_prompt_text(store):
    # 別名留在索引文件裡給 embedding 用，但不進 prompt，模型才不會照抄「（別名：…）」
    assert "別名" in store.retrieve("中正國小", n_results=1)[0].document
    with patch("config.RAG_MAX_DISTANCE", 2.0):  # 關鍵字 embedding 的距離很粗，這裡不測門檻
        text = store.search("中正國小", n_results=1)
    assert "別名" not in text
    assert "中正國小是花蓮地區的避難收容場所" in text


def test_rebuild_replaces_index(store):
    store.build_index(SHELTERS[:1])
    assert store.collection.count() == 1
    assert store.retrieve("台東", n_results=5)[0].name == "[YILAN] 宜蘭國小"


def test_empty_index_behaviour():
    s = VectorStore(embedding_function=KeywordEmbedding(), collection_name="test_vector_store_empty")
    assert s.retrieve("宜蘭") == []
    assert "沒有避難所資料" in s.search("宜蘭")


def make_store(path, name="persist_test"):
    return VectorStore(embedding_function=KeywordEmbedding(), collection_name=name, persist_path=str(path))


def test_persisted_index_survives_new_instance(tmp_path):
    make_store(tmp_path).build_index(SHELTERS)

    reopened = make_store(tmp_path)
    assert reopened.count() == len(SHELTERS)
    assert reopened.retrieve("台東 體育館", n_results=1)[0].name == "[TAITUNG] 台東縣立體育館"


def test_unchanged_data_skips_reembedding(tmp_path):
    make_store(tmp_path).build_index(SHELTERS)

    reopened = make_store(tmp_path)
    with patch.object(reopened.collection, "add") as add:
        reopened.build_index(SHELTERS)
        add.assert_not_called()


def test_changed_data_triggers_rebuild(tmp_path):
    make_store(tmp_path).build_index(SHELTERS)

    reopened = make_store(tmp_path)
    reopened.build_index(SHELTERS[:2])
    assert reopened.count() == 2


def test_force_rebuilds_even_when_unchanged(tmp_path):
    make_store(tmp_path).build_index(SHELTERS)

    class CountingEmbedding(KeywordEmbedding):
        calls = 0

        def __call__(self, input):
            CountingEmbedding.calls += 1
            return super().__call__(input)

    reopened = VectorStore(embedding_function=CountingEmbedding(), collection_name="persist_test", persist_path=str(tmp_path))
    old_collection = reopened.collection
    CountingEmbedding.calls = 0
    reopened.build_index(SHELTERS, force=True)
    # 強制重建真的重跑了 embedding，而且是換成新的 collection，不是在舊的上面刪了再加
    assert CountingEmbedding.calls >= 1
    assert reopened.collection is not old_collection
    assert reopened.count() == len(SHELTERS)


def test_memory_store_writes_no_fingerprint(tmp_path):
    store = VectorStore(embedding_function=KeywordEmbedding(), collection_name="memory_only", persist_path="")
    store.build_index(SHELTERS)
    assert store.count() == len(SHELTERS)
    assert list(tmp_path.iterdir()) == []


def test_upsert_updates_only_given_shelters(store):
    updated = Shelter("[HUALIEN] 中正國小", 400, 23.9, 121.6, 380, "花蓮縣花蓮市中正路210號")
    assert store.upsert_shelters([updated]) == 1
    assert store.count() == len(SHELTERS)
    hit = store.retrieve("中正國小", n_results=1)[0]
    assert "目前收容 380 人" in hit.document
    assert hit.metadata["remaining"] == 20
    other = store.retrieve("羅東鎮立體育館", n_results=1)[0]
    assert other.metadata["remaining"] == 700


def test_upsert_with_nothing_is_noop(store):
    assert store.upsert_shelters([]) == 0
    assert store.count() == len(SHELTERS)


def test_fingerprint_ignores_row_order(tmp_path):
    make_store(tmp_path).build_index(SHELTERS)

    reopened = make_store(tmp_path)
    with patch.object(reopened.collection, "add") as add:
        reopened.build_index(list(reversed(SHELTERS)))
        add.assert_not_called()


def test_upsert_keeps_fingerprint_in_sync(tmp_path):
    first = make_store(tmp_path)
    first.build_index(SHELTERS)
    updated = Shelter("[HUALIEN] 中正國小", 400, 23.9, 121.6, 380, "花蓮縣花蓮市中正路210號")
    first.upsert_shelters([updated])

    # 重啟後拿到的是資料庫裡的新佔用數，指紋要對得上，不該整批重建
    current = [updated if s.name == updated.name else s for s in SHELTERS]
    reopened = make_store(tmp_path)
    with patch.object(reopened.collection, "add") as add:
        reopened.build_index(current)
        add.assert_not_called()
    assert reopened.retrieve("中正國小", n_results=1)[0].metadata["current_people"] == 380


# ── metadata 篩選（B5）──────────────────────────────────────
from services.query_rules import analyze  # noqa: E402


def test_count_where(store):
    assert store.count_where(None) == len(SHELTERS)
    assert store.count_where({"region": "宜蘭"}) == 2
    assert store.count_where({"region": "高雄"}) == 0


def test_retrieve_with_where_returns_all_matching(store):
    hits = store.retrieve("避難所", where={"region": "宜蘭"})
    assert {h.name for h in hits} == {"[YILAN] 宜蘭國小", "[YILAN] 羅東鎮立體育館"}
    assert all(h.metadata["region"] == "宜蘭" for h in hits)


def test_retrieve_with_where_and_no_match_is_empty(store):
    assert store.retrieve("避難所", where={"region": "高雄"}) == []


def test_retrieve_planned_filters_by_region_and_facility(store):
    hits = store.retrieve_planned("宜蘭的體育館", analyze("宜蘭的體育館"))
    assert [h.name for h in hits] == ["[YILAN] 羅東鎮立體育館"]


def test_retrieve_planned_filters_by_capacity(store):
    hits = store.retrieve_planned("能收上千人的地方", analyze("能收上千人的地方"))
    assert [h.name for h in hits] == ["[TAITUNG] 台東縣立體育館"]


def test_retrieve_planned_orders_superlative_by_capacity(store):
    hits = store.retrieve_planned("最大的避難所", analyze("最大的避難所"))
    caps = [h.metadata["capacity"] for h in hits]
    assert caps == sorted(caps, reverse=True)
    assert hits[0].name == "[TAITUNG] 台東縣立體育館"


def test_ranking_is_exact_not_limited_to_semantic_top_k(store):
    # 語意 top-1 對「最大的避難所」抓到的未必是最大那間；排名要看整個索引
    with patch("config.RAG_TOP_K", 1):
        hits = store.retrieve_planned("最大的避難所", analyze("最大的避難所"))
    assert [h.name for h in hits][:2] == ["[TAITUNG] 台東縣立體育館", "[YILAN] 羅東鎮立體育館"]
    assert len(hits) == len(SHELTERS)


def test_ranking_respects_filter_and_direction(store):
    hits = store.retrieve_planned("宜蘭最大的避難所", analyze("宜蘭最大的避難所"))
    assert [h.name for h in hits] == ["[YILAN] 羅東鎮立體育館", "[YILAN] 宜蘭國小"]
    hits = store.retrieve_planned("宜蘭最小的避難所", analyze("宜蘭最小的避難所"))
    assert [h.name for h in hits] == ["[YILAN] 宜蘭國小", "[YILAN] 羅東鎮立體育館"]


def test_plan_retrieve_reports_filter_state(store):
    result = store.plan_retrieve("宜蘭的體育館", analyze("宜蘭的體育館"))
    assert (result.filtered, result.fell_back, result.ranked) == (True, False, False)
    result = store.plan_retrieve("宜蘭的圖書館", analyze("宜蘭的圖書館"))
    assert (result.filtered, result.fell_back) == (False, True)
    assert len(result.hits) > 0
    result = store.plan_retrieve("最大的避難所", analyze("最大的避難所"))
    assert (result.filtered, result.fell_back, result.ranked) == (False, False, True)
    result = store.plan_retrieve("避難所有提供飲水嗎", analyze("避難所有提供飲水嗎"))
    assert (result.filtered, result.fell_back, result.ranked) == (False, False, False)


def test_search_header_tells_model_about_filter_state(store):
    text = store.search("宜蘭的體育館", plan=analyze("宜蘭的體育館"))
    assert text.startswith("符合「宜蘭地區、體育場館」的避難所共 1 筆：")

    text = store.search("宜蘭的圖書館", plan=analyze("宜蘭的圖書館"))
    assert text.startswith("沒有找到符合「宜蘭地區、圖書館」條件的避難所。")
    assert "僅供參考" in text

    text = store.search("台東最大的避難所", plan=analyze("台東最大的避難所"))
    assert "依容量由大到小排序" in text.splitlines()[0]

    text = store.search("容量由小到大", plan=analyze("容量由小到大"))
    assert text.startswith("全東部避難所依容量由小到大排序")

    # 沒有任何條件的一般問題不加說明行（門檻另外測，這裡放寬）
    with patch("config.RAG_MAX_DISTANCE", 2.0):
        text = store.search("避難所有提供飲水嗎", plan=analyze("避難所有提供飲水嗎"))
    assert text.startswith("- ")


def test_retrieve_planned_falls_back_when_filter_matches_nothing(store):
    # 規則抽到「圖書館」但索引裡沒有，退回一般語意檢索而不是空手而回
    plan = analyze("宜蘭的圖書館")
    assert plan.to_where() is not None
    hits = store.retrieve_planned("宜蘭的圖書館", plan)
    assert len(hits) > 0


def test_search_with_plan_returns_only_filtered_docs(store):
    text = store.search("台東的體育館", plan=analyze("台東的體育館"))
    assert "台東縣立體育館" in text
    assert "羅東" not in text


class GatedEmbedding(KeywordEmbedding):
    """重建索引時把 embedding 卡住，讓測試在「重建進行中」的時間點去查舊索引"""

    def __init__(self):
        import threading
        self.gate = threading.Event()
        self.started = threading.Event()
        self.blocking = False

    def __call__(self, input):
        if self.blocking:
            self.started.set()
            self.gate.wait(timeout=5)
        return super().__call__(input)


def test_old_index_stays_queryable_while_rebuilding():
    import threading
    ef = GatedEmbedding()
    store = VectorStore(embedding_function=ef, collection_name="test_rebuild_window")
    store.build_index(SHELTERS[:2])
    assert store.count() == 2

    ef.blocking = True
    worker = threading.Thread(target=store.build_index, args=(SHELTERS, True))
    worker.start()
    assert ef.started.wait(timeout=5)
    # 舊版做法是先刪光再加回，這時 count 會是 0、問答會回「目前沒有避難所資料」
    assert store.count() == 2
    assert [h.name for h in store.rank_by_capacity(None)] == ["[YILAN] 羅東鎮立體育館", "[YILAN] 宜蘭國小"]
    ef.gate.set()
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert store.count() == 4
    # 暫存 collection 不能留下來
    assert "test_rebuild_window__rebuild" not in [c.name for c in store.client.list_collections()]


def test_rebuild_cleans_up_staging_when_embedding_fails():
    class BrokenEmbedding(KeywordEmbedding):
        def __init__(self):
            self.fail = False

        def __call__(self, input):
            if self.fail:
                raise RuntimeError("embedding down")
            return super().__call__(input)

    ef = BrokenEmbedding()
    store = VectorStore(embedding_function=ef, collection_name="test_rebuild_failure")
    store.build_index(SHELTERS[:2])
    ef.fail = True
    with pytest.raises(RuntimeError):
        store.build_index(SHELTERS, force=True)
    # 舊索引原封不動，暫存 collection 也清掉了
    assert store.count() == 2
    assert "test_rebuild_failure__rebuild" not in [c.name for c in store.client.list_collections()]


def test_leftover_staging_collection_is_dropped_on_startup(tmp_path):
    first = make_store(tmp_path, name="leftover")
    first.build_index(SHELTERS)
    first.client.get_or_create_collection("leftover__rebuild")
    second = make_store(tmp_path, name="leftover")
    assert "leftover__rebuild" not in [c.name for c in second.client.list_collections()]
    assert second.count() == len(SHELTERS)


def test_persisted_rebuild_is_visible_to_a_new_instance(tmp_path):
    store = make_store(tmp_path, name="persist_rebuild")
    store.build_index(SHELTERS[:2])
    store.build_index(SHELTERS, force=True)
    again = make_store(tmp_path, name="persist_rebuild")
    assert again.count() == len(SHELTERS)
    assert again.build_index(SHELTERS) is None  # 指紋相符，不重建


def test_metadata_has_road_without_section(store):
    hit = store.retrieve("台東縣立體育館", n_results=1)[0]
    assert hit.metadata["road"] == "桂林北路"


def test_road_filter_returns_every_shelter_on_that_road(store):
    from services.query_rules import analyze
    result = store.plan_retrieve("崇聖街", analyze("崇聖街"))
    assert result.filtered and [h.name for h in result.hits] == ["[YILAN] 宜蘭國小"]


def test_named_query_keeps_only_the_named_shelter(store):
    from services.query_rules import analyze
    # 直接點名時只留那一筆，小模型才不會把 top-k 全部列出來
    result = store.plan_retrieve("中正國小還有空位嗎", analyze("中正國小還有空位嗎"))
    assert result.named
    assert [h.name for h in result.hits] == ["[HUALIEN] 中正國小"]
    text = store.search("中正國小還有空位嗎", plan=analyze("中正國小還有空位嗎"))
    assert text.startswith("使用者詢問的避難所資料如下（共 1 筆）")
    assert "羅東" not in text


def test_far_semantic_hits_are_treated_as_no_match(store):
    from services.query_rules import analyze
    from services.vector_store import NO_MATCH
    # 沒有任何關鍵字的問題：最接近的文件也離很遠，純語意路徑要回沒有資料
    assert store.search("今天天氣如何", plan=analyze("今天天氣如何")) == NO_MATCH
    # 有 metadata 篩選命中的路徑不套門檻
    assert "宜蘭國小" in store.search("宜蘭有哪些", plan=analyze("宜蘭有哪些"))
    # 門檻放寬到最大時，同一個問題就會回資料
    with patch("config.RAG_MAX_DISTANCE", 2.0):
        assert store.search("今天天氣如何", plan=analyze("今天天氣如何")) != NO_MATCH


def lock_is_free(store) -> bool:
    """
    從另一條執行緒看 store 的鎖現在是不是空的。
    _lock 是 RLock，同一條執行緒可以重入，所以在原執行緒上測不出有沒有被持有。
    """
    got = []

    def probe():
        acquired = store._lock.acquire(blocking=False)
        got.append(acquired)
        if acquired:
            store._lock.release()

    t = threading.Thread(target=probe)
    t.start()
    t.join()
    return got[0]


def watch_embedding_lock(store, seen: list):
    """把 store.ef 換成會在算 embedding 時記下鎖狀態的版本"""
    base = store.ef

    class ProbingEmbedding:
        def __call__(self, input):
            seen.append(lock_is_free(store))
            return base(input)

        def name(self):
            return base.name()

    store.ef = ProbingEmbedding()


def test_query_embedding_runs_outside_the_lock(store):
    # 查詢 embedding 要打 Ollama（最久 EMBEDDING_TIMEOUT 秒）。持鎖做的話，
    # 同一把鎖上的 count() 會跟著排隊，readiness 與容器 healthcheck 一起被拖垮
    seen = []
    watch_embedding_lock(store, seen)
    hits = store.retrieve("宜蘭 國小", n_results=2)
    assert len(hits) == 2
    assert seen == [True]


def test_upsert_embedding_runs_outside_the_lock(store):
    seen = []
    watch_embedding_lock(store, seen)
    updated = Shelter("[HUALIEN] 中正國小", 400, 23.9, 121.6, 380, "花蓮縣花蓮市中正路210號")
    assert store.upsert_shelters([updated]) == 1
    assert seen == [True]
    # 文件與向量還是要真的換掉
    assert store.retrieve("中正國小", n_results=1)[0].metadata["current_people"] == 380


def test_plan_retrieve_reports_total_even_when_truncated(store):
    plan = analyze("宜蘭的避難所")
    result = store.plan_retrieve("宜蘭的避難所", plan)
    assert (len(result.hits), result.total) == (2, 2)
    with patch("services.vector_store.MAX_FILTERED_RESULTS", 1):
        result = store.plan_retrieve("宜蘭的避難所", plan)
    assert (len(result.hits), result.total) == (1, 2)


def test_header_reports_total_not_just_what_fits(store):
    # 送進 prompt 的筆數被上限截掉時，標頭說「共 1 筆」等於叫模型回答錯的總數
    with patch("services.vector_store.MAX_FILTERED_RESULTS", 1):
        text = store.search("宜蘭的避難所", plan=analyze("宜蘭的避難所"))
    assert text.startswith("符合「宜蘭地區」的避難所共 2 筆，以下列出最相關的 1 筆：")
    assert text.count("\n") == 1


def test_ranked_header_reports_total_not_just_what_fits(store):
    with patch("services.vector_store.MAX_RANKED_RESULTS", 1):
        text = store.search("宜蘭最大的避難所", plan=analyze("宜蘭最大的避難所"))
    assert text.startswith("符合「宜蘭地區」的避難所共 2 筆，以下是依容量由大到小排序的前 1 筆：")
    assert "羅東鎮立體育館" in text
    assert text.count("\n") == 1


def test_slow_embedding_does_not_block_readiness_count(store):
    """
    readiness 檢查索引走的是 count()。embedding 卡在 Ollama 上時它必須還能回答，
    否則容器 healthcheck 的 10 秒會在 Ollama 一慢就把服務判成 unhealthy。
    """
    embedding_started = threading.Event()
    release_embedding = threading.Event()
    base = store.ef

    class BlockingEmbedding:
        def __call__(self, input):
            embedding_started.set()
            assert release_embedding.wait(timeout=10), "測試自己卡住了"
            return base(input)

        def name(self):
            return base.name()

    store.ef = BlockingEmbedding()
    retrieved = []
    searcher = threading.Thread(target=lambda: retrieved.append(len(store.retrieve("宜蘭 國小", n_results=2))))
    searcher.start()
    try:
        assert embedding_started.wait(timeout=10)
        # count() 另開執行緒跑：萬一 embedding 又被包回鎖裡，這裡要是失敗而不是整個卡死
        counted = []
        done = threading.Event()

        def counter():
            counted.append(store.count())
            done.set()

        threading.Thread(target=counter, daemon=True).start()
        assert done.wait(timeout=5), "embedding 還在跑的時候 count() 被鎖住了"
        assert counted == [len(SHELTERS)]
    finally:
        release_embedding.set()
        searcher.join(timeout=10)
    assert retrieved == [2]
