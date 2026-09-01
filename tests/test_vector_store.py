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
    Shelter("[YILAN] 宜蘭國小", 500, 24.7, 121.7, 0),
    Shelter("[YILAN] 羅東鎮立體育館", 800, 24.6, 121.7, 100),
    Shelter("[HUALIEN] 中正國小", 400, 23.9, 121.6, 0),
    Shelter("[TAITUNG] 台東縣立體育館", 2000, 22.7, 121.1, 0),
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
    assert meta["capacity"] == 800
    assert meta["remaining"] == 700
    assert meta["occupancy_rate"] == 12.5


def test_document_has_no_region_tag(store):
    hit = store.retrieve("中正國小", n_results=1)[0]
    assert "HUALIEN" not in hit.document
    assert "花蓮的中正國小" in hit.document


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
