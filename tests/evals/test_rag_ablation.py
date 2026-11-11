from evals.run_rag_eval import rules_only
from services.chat.query_rules import QueryPlan
from services.retrieval.vector_store import OVERVIEW_LIST, Hit


class FakeStore:
    """只有 metadata 查詢；誰呼叫了向量檢索就直接失敗"""

    def __init__(self, shelters, named=()):
        self.shelters = shelters
        self.named = list(named)

    def rank_by_capacity(self, where, descending=True):
        hits = [Hit(name=n, document="", distance=0.0, metadata={"capacity": c}) for n, c in self.shelters]
        return sorted(hits, key=lambda h: h.metadata["capacity"], reverse=descending)

    def exact_named(self, query):
        return [Hit(name=n, document="", distance=0.0, metadata={}) for n in self.named if n in query]

    def retrieve(self, *args, **kwargs):
        raise AssertionError("rules-only must not use the vector index")

    retrieve_planned = retrieve_with_total = retrieve


def test_a_named_shelter_wins_over_the_township_filter():
    store = FakeStore([("甲", 300), ("乙", 100)], named=["中正國小"])
    hits = rules_only(store, "吉安鄉中正國小容量多少", QueryPlan(township="吉安鄉"))
    assert [h.name for h in hits] == ["中正國小"]


def test_a_filter_is_ordered_by_capacity_and_cut_like_the_overview():
    store = FakeStore([(f"s{i}", i) for i in range(40)])
    hits = rules_only(store, "花蓮有哪些避難所", QueryPlan(region="花蓮"))
    assert len(hits) == OVERVIEW_LIST
    assert hits[0].name == "s39"


def test_without_a_filter_or_a_name_nothing_is_found():
    assert rules_only(FakeStore([("甲", 300)]), "哪裡可以帶寵物", QueryPlan()) == []
