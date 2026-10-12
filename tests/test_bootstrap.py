import json

import pytest

from evals.compare_runs import compare, load_rows
from evals.metrics import bootstrap_ci, paired_bootstrap_ci, stratified_bootstrap_ci


def test_ci_is_reproducible_for_a_fixed_seed():
    # 論文裡的信賴區間每跑一次就換一個數字是不能用的
    values = [0.0, 1.0] * 30
    assert bootstrap_ci(values, iterations=200, seed=7) == bootstrap_ci(values, iterations=200, seed=7)
    assert bootstrap_ci(values, iterations=200, seed=7) != bootstrap_ci(values, iterations=200, seed=8)


def test_ci_brackets_the_sample_mean():
    values = [0.6, 0.8, 1.0, 0.9, 0.7, 0.85, 0.95, 0.75]
    lo, hi = bootstrap_ci(values, iterations=500)
    assert lo <= sum(values) / len(values) <= hi


def test_identical_values_give_a_degenerate_interval():
    lo, hi = bootstrap_ci([0.5] * 20, iterations=200)
    assert (lo, hi) == (0.5, 0.5)


def test_smaller_samples_give_wider_intervals():
    # 這就是「130 題、差 0.03」的問題：樣本越小，區間越寬
    mixed = [0.0, 1.0]
    small = bootstrap_ci(mixed * 5, iterations=500)
    large = bootstrap_ci(mixed * 100, iterations=500)
    assert (small[1] - small[0]) > (large[1] - large[0])


def test_empty_input_is_safe():
    assert bootstrap_ci([]) == (0.0, 0.0)
    assert stratified_bootstrap_ci([]) == (0.0, 0.0)
    assert stratified_bootstrap_ci([[], []]) == (0.0, 0.0)
    assert paired_bootstrap_ci([]) == (0.0, 0.0)


def test_stratified_ci_brackets_the_macro_mean():
    # macro 是「先算每類平均、再對類別取平均」，所以大類別不會把小類別蓋掉
    groups = [[1.0] * 50, [0.0] * 2]
    macro = (1.0 + 0.0) / 2
    lo, hi = stratified_bootstrap_ci(groups, iterations=300)
    assert lo <= macro <= hi
    # 兩個類別內部都沒有變異，所以區間退化成一個點
    assert (lo, hi) == (0.5, 0.5)


def test_paired_ci_cancels_question_difficulty():
    """
    配對重抽的重點：同一題在兩個設定下的分數綁在一起抽，題目難度造成的變異會消掉。

    這裡兩個設定在每一題上都剛好差 0.1，但題目本身從 0.1 變化到 0.9。
    各自獨立算區間會很寬，配對之後差值的區間是一個點。
    """
    pairs = [(v + 0.1, v) for v in (0.1, 0.3, 0.5, 0.7, 0.9)]
    assert paired_bootstrap_ci(pairs, iterations=300) == (0.1, 0.1)
    a_only = bootstrap_ci([a for a, _ in pairs], iterations=300)
    assert (a_only[1] - a_only[0]) > 0.1


def _write(path, rows):
    path.write_text(json.dumps({"rows": rows}, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_load_rows_rejects_duplicate_queries(tmp_path):
    # 題目重複就沒辦法逐題配對，要明確失敗而不是默默配錯
    path = _write(tmp_path / "dup.json", [
        {"query": "同一題", "scores": {"mrr": 1.0}},
        {"query": "同一題", "scores": {"mrr": 0.0}},
    ])
    with pytest.raises(SystemExit) as err:
        load_rows(path)
    assert "重複" in str(err.value)


def test_compare_uses_only_shared_questions(tmp_path):
    a = _write(tmp_path / "a.json", [
        {"query": "共用1", "scores": {"mrr": 1.0}},
        {"query": "共用2", "scores": {"mrr": 1.0}},
        {"query": "只在 A", "scores": {"mrr": 0.0}},
    ])
    b = _write(tmp_path / "b.json", [
        {"query": "共用1", "scores": {"mrr": 0.5}},
        {"query": "共用2", "scores": {"mrr": 0.5}},
    ])
    result = compare(load_rows(a), load_rows(b), ["mrr"], iterations=200, seed=1)
    assert (result["shared"], result["only_in_a"], result["only_in_b"]) == (2, 1, 0)
    m = result["metrics"]["mrr"]
    assert (m["n"], m["a"], m["b"], m["diff"]) == (2, 1.0, 0.5, 0.5)
    assert m["significant"] is True


def test_zero_difference_is_reported_as_indistinguishable(tmp_path):
    # 區間包含 0 的意思是「這份題庫看不出差異」，不是「兩者相同」
    a = _write(tmp_path / "a.json", [{"query": f"q{i}", "scores": {"mrr": i / 10}} for i in range(10)])
    b = _write(tmp_path / "b.json", [{"query": f"q{i}", "scores": {"mrr": i / 10}} for i in range(10)])
    result = compare(load_rows(a), load_rows(b), ["mrr"], iterations=200, seed=1)
    m = result["metrics"]["mrr"]
    assert m["diff"] == 0.0
    assert m["ci95"] == [0.0, 0.0]
    assert m["significant"] is False


def test_missing_metric_is_skipped_not_faked(tmp_path):
    a = _write(tmp_path / "a.json", [{"query": "q", "scores": {"mrr": 1.0}}])
    b = _write(tmp_path / "b.json", [{"query": "q", "scores": {"recall@3": 1.0}}])
    result = compare(load_rows(a), load_rows(b), ["mrr", "recall@3"], iterations=100, seed=1)
    assert result["metrics"] == {}
