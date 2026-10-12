from evals.metrics import (
    aggregate,
    hit_at_k,
    percentile,
    precision_at_k,
    question_group,
    r_precision,
    recall_at_k,
    recall_ceiling_at_k,
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


def test_r_precision_is_not_capped_by_k():
    # 20 份相關文件、只回傳 10 筆時 recall@10 最高只有 0.5；
    # R-Precision 的 K 取 |rel|，所以它量到的是「前 20 名裡命中幾個」
    relevant = {f"s{i}" for i in range(20)}
    ranked = [f"s{i}" for i in range(10)]
    assert recall_at_k(ranked, relevant, 10) == 0.5
    assert r_precision(ranked, relevant) == 0.5
    # 全部回傳且全部正確時兩者都是 1
    full = [f"s{i}" for i in range(20)]
    assert r_precision(full, relevant) == 1.0


def test_recall_ceiling_exposes_saturation():
    # 這就是 region 類的情況：14 到 20 份相關文件，recall@10 再怎麼完美也到不了 1
    assert recall_ceiling_at_k({f"s{i}" for i in range(20)}, 10) == 0.5
    assert recall_ceiling_at_k({f"s{i}" for i in range(14)}, 10) == 10 / 14
    # |rel| <= k 時沒有天花板問題
    assert recall_ceiling_at_k({"a"}, 10) == 1.0
    assert recall_ceiling_at_k({"a", "b"}, 10) == 1.0
    assert recall_ceiling_at_k(set(), 10) == 1.0


def test_perfect_retrieval_hits_the_ceiling_exactly():
    # 完美檢索時 recall@k 會剛好等於天花板。兩個數字並排報，
    # 讀者才分得出「檢索不好」和「指標撞到上限」
    relevant = {f"s{i}" for i in range(16)}
    ranked = [f"s{i}" for i in range(16)]
    scored = score_case(ranked, relevant, [3, 10])
    assert scored["recall@10"] == scored["recall_ceiling@10"] == 10 / 16
    assert scored["r_precision"] == 1.0
    assert scored["recall@all"] == 1.0


def test_question_group_splits_by_answer_count():
    assert question_group({"a"}) == "single"
    assert question_group({"a", "b"}) == "multi"
    assert question_group(set()) == "negative"
