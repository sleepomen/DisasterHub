from evals.metrics import (
    aggregate,
    hit_at_k,
    percentile,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    score_case,
)

RANKED = ["a", "b", "c", "d", "e"]


def test_recall_at_k():
    assert recall_at_k(RANKED, {"a", "c", "z"}, 3) == 2 / 3
    assert recall_at_k(RANKED, {"z"}, 5) == 0.0
    assert recall_at_k(RANKED, set(), 5) == 1.0


def test_precision_at_k():
    assert precision_at_k(RANKED, {"a", "c"}, 3) == 2 / 3
    assert precision_at_k(RANKED, {"a"}, 0) == 0.0


def test_reciprocal_rank():
    assert reciprocal_rank(RANKED, {"c"}) == 1 / 3
    assert reciprocal_rank(RANKED, {"a", "c"}) == 1.0
    assert reciprocal_rank(RANKED, {"z"}) == 0.0


def test_hit_at_k():
    assert hit_at_k(RANKED, {"d"}, 3) == 0.0
    assert hit_at_k(RANKED, {"d"}, 5) == 1.0


def test_percentile():
    vals = [10, 20, 30, 40, 50]
    assert percentile(vals, 0.5) == 30
    assert percentile(vals, 0.95) == 50
    assert percentile([], 0.5) == 0.0


def test_score_case_and_aggregate():
    s1 = score_case(RANKED, {"a"}, [1, 3])
    s2 = score_case(RANKED, {"e"}, [1, 3])
    assert s1["recall@1"] == 1.0 and s2["recall@1"] == 0.0
    agg = aggregate([s1, s2])
    assert agg["recall@1"] == 0.5
    assert agg["mrr"] == (1.0 + 0.2) / 2
    assert aggregate([]) == {}


def test_score_case_includes_full_list_metrics():
    ranked = ["a", "b", "c", "d"]
    scores = score_case(ranked, {"a", "c", "z"}, [2])
    assert scores["recall@all"] == 2 / 3
    assert scores["precision@all"] == 2 / 4
    assert score_case([], {"a"}, [2])["precision@all"] == 0.0
    assert score_case([], {"a"}, [2])["recall@all"] == 0.0
