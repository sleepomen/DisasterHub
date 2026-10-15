import json
import pathlib

from evals.run_boundary_eval import classify, load_cases, summarize

CASES_PATH = pathlib.Path(__file__).resolve().parent.parent / "evals" / "boundary_eval.jsonl"


class StubStore:
    """只回固定結果：這個測試驗的是「哪一層決定的」歸因，不是檢索品質"""

    def __init__(self, refuse=False):
        self.refuse = refuse

    def plan_retrieve(self, query, plan, n_results=None):
        from services.vector_store import Hit, PlannedResult
        hit = Hit(name="[YILAN] 測試", document="doc", distance=0.3, metadata={"name": "[YILAN] 測試"})
        return PlannedResult([hit], total=1, filtered=plan.to_where() is not None)

    def search(self, query, n_results=None, plan=None):
        from services.vector_store import NO_MATCH
        return NO_MATCH if self.refuse else "資料"


def test_layer_attribution_follows_build_context_order():
    """
    歸因順序必須跟 build_context 一致。第一版漏了 off_topic 這一層，
    於是「花蓮的天氣」被報成「LLM 攔下的」，而實際上它在檢索之前就被拒答了——
    這支腳本的全部價值就在這個欄位，錯了比沒有更糟。
    """
    store = StubStore()
    assert classify(store, "高雄的避難所")["layer"] == "rules"
    assert classify(store, "花蓮的天氣")["layer"] == "off_topic"
    assert classify(store, "宜蘭有哪些避難所")["layer"] == "llm"
    assert classify(StubStore(refuse=True), "避難所有提供飲水嗎")["layer"] == "distance"


def test_rules_layers_report_no_distance():
    # 這兩層在算距離之前就決定了，報一個距離出來會讓人以為門檻有參與
    for query in ("高雄的避難所", "花蓮的天氣"):
        row = classify(StubStore(), query)
        assert row["top1_distance"] is None
        assert row["refused"] is True


def test_cases_are_well_formed():
    cases = load_cases(CASES_PATH)
    assert len(cases) >= 20
    for c in cases:
        assert c["query"].strip() and c["note"].strip()
        assert c["expect"] in ("refuse", "answer", "refuse_or_answer")
    # 必須同時有該拒答與該回答的題目，否則只量到單邊
    expects = {c["expect"] for c in cases}
    assert {"refuse", "answer"} <= expects


def test_ambiguous_cases_are_excluded_from_the_score():
    rows = [
        {"query": "a", "expect": "refuse", "kind": "x", "layer": "rules", "refused": True,
         "filtered": None, "top1_distance": None},
        {"query": "b", "expect": "refuse_or_answer", "kind": "y", "layer": "llm", "refused": False,
         "filtered": True, "top1_distance": 0.3},
    ]
    s = summarize(rows)
    assert (s["count"], s["scored"], s["correct"]) == (2, 1, 1)
    assert s["accuracy"] == 1.0


def test_results_file_matches_the_case_file_if_present():
    out = CASES_PATH.parent / "results" / "boundary_eval.json"
    if not out.is_file():
        return
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["summary"]["count"] == len(load_cases(CASES_PATH))
