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

    def search_result(self, query, n_results=None, plan=None):
        from services.vector_store import SearchResult
        return SearchResult(self.search(query, n_results, plan))

    def shelter_metadata(self):
        return [{"name": "[YILAN] 宜蘭國小"}, {"name": "[YILAN] 頭城國小"}]


def test_layer_attribution_follows_build_context_order():
    """
    歸因順序必須跟 build_context 一致。第一版漏了 off_topic 這一層，
    於是「花蓮的天氣」被報成「LLM 攔下的」，而實際上它在檢索之前就被拒答了——
    這支腳本的全部價值就在這個欄位，錯了比沒有更糟。
    """
    store = StubStore()
    assert classify(store, "高雄的避難所")["layer"] == "rules"
    assert classify(store, "花蓮的天氣")["layer"] == "off_topic"
    assert classify(store, "頭城國中在哪裡")["layer"] == "unknown_name"
    assert classify(store, "頭城國小在哪裡")["layer"] == "llm"
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


def test_nonexistent_shelter_is_judged_by_acknowledgement_not_by_abstain_markers():
    """
    問不存在的場所時，「資料中沒有新生高中，最接近的是新生國中…」是對的回答，
    但它不含 ABSTAIN_MARKERS；反過來直接答「新生國中：容量 600 人」看起來很正常，其實是換答
    """
    from evals.gen_metrics import build_catalog
    from evals.run_boundary_eval import judge_nonexistent
    from models.shelter import Shelter

    catalog = build_catalog([Shelter("[TAITUNG] 新生國中", 600, 22.7, 121.1, 0, "台東縣台東市")])

    def verdict(reply):
        return judge_nonexistent(reply, "新生高中", catalog)["verdict"]

    assert verdict("資料中沒有新生高中，最接近的是新生國中，容量 600 人。") == "acknowledged"
    assert verdict("新生國中：台東市，容量 600 人，剩餘空位 600 人。") == "substituted"
    # 「X：無」既沒交代也沒換答，兩邊都不算對
    assert verdict("新生高中：無") == "other"


def test_describing_the_nonexistent_shelter_is_fabrication_even_with_a_disclaimer_later():
    """
    llama3.2:3b 實測：「頭城國中：宜蘭縣頭城鎮，容量 400 人。目前沒有完全符合條件的避難所。」
    400 人是頭城國小的資料。後面那句交代救不回前面已經編好的一筆，不能算對
    """
    from evals.gen_metrics import build_catalog
    from evals.run_boundary_eval import judge_nonexistent

    reply = "頭城國中：宜蘭縣頭城鎮，容量 400 人，剩餘空位 400 人。\n目前沒有完全符合「頭城鎮、國中」條件的避難所。"
    assert judge_nonexistent(reply, "頭城國中", build_catalog([]))["verdict"] == "fabricated"
    # 狀態也算：說它「無空位」一樣是替不存在的場所編出資料
    assert judge_nonexistent("礁溪國小：無空位。", "礁溪國小", build_catalog([]))["verdict"] == "fabricated"
    # 「沒有空位」裡的「沒有」不是在說沒有這間（實測第二輪就被這樣誤判成交代）
    assert judge_nonexistent("知本國中：沒有空位。", "知本國中", build_catalog([]))["verdict"] == "fabricated"
    # 句子本身就在說沒有這間 → 不是編造
    assert judge_nonexistent("東海高中：無資料", "東海高中", build_catalog([]))["verdict"] == "acknowledged"


def test_named_answer_must_name_the_shelter_and_use_its_numbers():
    """錯字題實際出過的錯：把涙強國小答成 250 人，濤強國小實際是 300。只看「沒拒答」會算對"""
    from evals.gen_metrics import build_catalog
    from evals.run_boundary_eval import judge_named_answer, row_correct
    from models.shelter import Shelter

    target = Shelter("[HUALIEN] 濤強國小", 300, 23.9, 121.6, 0, "花蓮縣花蓮市")
    other = Shelter("[HUALIEN] 明禮國小", 250, 23.9, 121.6, 0, "花蓮縣花蓮市")
    catalog = build_catalog([target, other])

    ok = judge_named_answer("（找不到「涙強國小」，以下是名稱最接近的「濤強國小」的資料）\n濤強國小：容量 300 人", target, catalog)
    assert ok["answer_ok"]
    wrong_number = judge_named_answer("濤強國小：容量 250 人", target, catalog)
    assert not wrong_number["answer_ok"] and wrong_number["wrong_numbers"] == [250]
    wrong_shelter = judge_named_answer("明禮國小：容量 250 人", target, catalog)
    assert not wrong_shelter["named_ok"]

    row = {"expect": "answer", "refused": False, **wrong_number}
    assert not row_correct(row)



def test_house_number_in_the_targets_address_is_not_a_wrong_number():
    """實測「中正國小：花蓮縣花蓮市中正路210號，容量 187 人」被判數字錯——210 是門牌"""
    from evals.gen_metrics import build_catalog
    from evals.run_boundary_eval import judge_named_answer
    from models.shelter import Shelter

    target = Shelter("[HUALIEN] 中正國小", 187, 23.9, 121.6, 0, "花蓮縣花蓮市中正路210號")
    catalog = build_catalog([target])
    result = judge_named_answer("中正國小：花蓮縣花蓮市中正路210號，容量 187 人，剩餘空位 187 人。", target, catalog)
    assert result["answer_ok"] and result["wrong_numbers"] == []
