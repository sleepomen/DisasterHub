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

    reopened = make_store(tmp_path)
    with patch.object(reopened.collection, "add", wraps=reopened.collection.add) as add:
        reopened.build_index(SHELTERS, force=True)
        add.assert_called_once()


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

    # 沒有任何條件的一般問題不加說明行
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
