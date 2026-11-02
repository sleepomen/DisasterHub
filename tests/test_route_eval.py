import json
import pathlib

import pytest

from evals.run_route_eval import load_cases, summarize
from services.chat_service import ROUTE_COUNTERS
from services.metrics import ROUTE_PREFIX

CASES_PATH = pathlib.Path(__file__).resolve().parent.parent / "evals" / "route_eval.jsonl"

# 程式真的會記錄的路由名稱。context_error 是例外路徑而不是路由決策，評測集不涵蓋
KNOWN_ROUTES = {name[len(ROUTE_PREFIX):] for name in ROUTE_COUNTERS} - {"context_error"}


def test_every_expected_route_exists_in_the_code():
    # 評測集裡打錯字的路由名稱會永遠被算成誤路由，而且看起來像系統的錯
    cases = load_cases(CASES_PATH)
    labelled = {c["expected_route"] for c in cases}
    assert labelled <= KNOWN_ROUTES, labelled - KNOWN_ROUTES


def test_route_eval_set_covers_every_route():
    """
    rag_eval.jsonl 的 130 題只碰到 rag 與 out_of_scope 兩條路由，
    nearest / needs_coords / simulation / no_simulation_advice 四條完全沒被測過——
    而後兩條正是「混合路由」這個設計主張的內容。這個測試防止覆蓋率再退回去。
    """
    cases = load_cases(CASES_PATH)
    labelled = {c["expected_route"] for c in cases}
    assert labelled == KNOWN_ROUTES, KNOWN_ROUTES - labelled


def test_cases_are_well_formed():
    cases = load_cases(CASES_PATH)
    assert len(cases) >= 30
    for c in cases:
        assert c["query"].strip()
        assert isinstance(c["simulation"], bool)
        # 每一題都要寫理由：路由的期望值是人的判斷，沒寫理由就無法複查
        assert c["note"].strip()


def test_eval_set_covers_state_dependent_routing():
    """
    路由必須看狀態而不只看關鍵字，所以評測集要有同一句話在有／沒有模擬時期望不同路由
    （「請給我疏散建議」：沒有模擬 → no_simulation_advice，模擬中 → simulation）。

    反過來，同一句在兩種狀態下期望「相同」路由也是刻意的：「哪些避難所受到影響」
    不論有沒有模擬都該走模擬路徑，由它自己回「尚未執行模擬」，不要掉進語意檢索。
    """
    cases = load_cases(CASES_PATH)
    routes_by_state = {}
    for c in cases:
        routes_by_state.setdefault(c["query"], {})[c["simulation"]] = c["expected_route"]
    both_states = {q: r for q, r in routes_by_state.items() if len(r) == 2}
    assert both_states, "評測集應該有題目同時標了有模擬與沒有模擬兩種狀態"
    differing = [q for q, r in both_states.items() if len(set(r.values())) > 1]
    assert differing, "至少要有一題在不同模擬狀態下期望不同路由，否則沒測到狀態相依性"


def test_summarize_counts_and_builds_a_confusion_matrix():
    rows = [
        {"query": "a", "expected": "rag", "actual": "rag", "simulation": False, "note": ""},
        {"query": "b", "expected": "rag", "actual": "needs_coords", "simulation": False, "note": ""},
        {"query": "c", "expected": "nearest", "actual": "nearest", "simulation": False, "note": ""},
    ]
    s = summarize(rows)
    assert (s["count"], s["correct"]) == (3, 2)
    assert s["accuracy"] == pytest.approx(2 / 3, abs=1e-4)
    assert s["per_route"]["rag"] == {"count": 2, "correct": 1, "accuracy": 0.5}
    assert s["confusion"]["rag"] == {"rag": 1, "needs_coords": 1}
    assert s["confusion"]["nearest"] == {"nearest": 1}


def test_results_file_is_valid_json_if_present():
    out = CASES_PATH.parent / "results" / "route_eval.json"
    if not out.is_file():
        pytest.skip("還沒跑過路由評測")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["summary"]["count"] == len(load_cases(CASES_PATH))
