import random
from statistics import mean


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 1.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def precision_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if k == 0:
        return 0.0
    return len(set(ranked[:k]) & relevant) / k


def r_precision(ranked: list[str], relevant: set[str]) -> float:
    """
    K 取「該題的相關文件數」的 precision。|rel| > K 時 recall@K 的上限不是 1，
    R-Precision 沒有這個問題，所以列舉題要用它而不是 recall@10。
    """
    if not relevant:
        return 1.0
    k = len(relevant)
    return len(set(ranked[:k]) & relevant) / k


def recall_ceiling_at_k(relevant: set[str], k: int) -> float:
    """
    recall@k 在這一題上的理論上限 min(k, |rel|) / |rel|。

    |rel| > k 時它小於 1——代表就算檢索完美，recall@k 也到不了 1。
    把它跟 recall@k 一起報，才看得出一個數字是「量到的能力」還是「撞到的天花板」：
    本專案 region 類的 recall@10 是 0.6131，而天花板正好是 0.613。
    """
    if not relevant:
        return 1.0
    return min(k, len(relevant)) / len(relevant)


def question_group(relevant) -> str:
    """
    依「正確答案有幾份」把題目分群，因為兩群適用的指標不同：

    single   只有一份正確文件。recall@k 等於 hit@k，排序類指標（MRR）有意義。
    multi    多份正確文件（列舉題）。recall@k 被 min(k,|rel|)/|rel| 的上限壓住，
             要看集合類指標：R-Precision、recall@all、precision@all。
    negative 沒有正確文件，本來就另外處理（拒答率）。

    刻意按題目的 |rel| 分而不是按類別分：township 與 capacity 兩類本身就混了
    單答案與多答案的題目，按類別分會把單答案的題目也打上「受天花板限制」的標籤。
    """
    n = len(relevant)
    if n == 0:
        return "negative"
    return "single" if n == 1 else "multi"


def reciprocal_rank(ranked: list[str], relevant: set[str]) -> float:
    for i, name in enumerate(ranked, 1):
        if name in relevant:
            return 1.0 / i
    return 0.0


def hit_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    return 1.0 if set(ranked[:k]) & relevant else 0.0


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * p)))
    return ordered[idx]


# bootstrap 預設：1000 次重抽樣、95% 區間、固定種子。
# 種子一定要固定——論文裡的信賴區間每跑一次就變一個數字是不能用的
BOOTSTRAP_ITERATIONS = 1000
BOOTSTRAP_CONFIDENCE = 0.95
BOOTSTRAP_SEED = 20261012


def bootstrap_ci(
    values: list[float],
    iterations: int = BOOTSTRAP_ITERATIONS,
    confidence: float = BOOTSTRAP_CONFIDENCE,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """
    對「平均值」做 bootstrap 信賴區間：每次從原樣本抽同樣數量（可重複）算一次平均，
    重複 iterations 次，取這些平均值分布的兩端。

    130 題、50 間避難所的規模下，兩個設定差 0.03 到底有沒有意義，只有區間答得出來。
    """
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    means = [mean(rng.choices(values, k=n)) for _ in range(iterations)]
    tail = (1.0 - confidence) / 2
    return round(percentile(means, tail), 4), round(percentile(means, 1.0 - tail), 4)


def stratified_bootstrap_ci(
    groups: list[list[float]],
    iterations: int = BOOTSTRAP_ITERATIONS,
    confidence: float = BOOTSTRAP_CONFIDENCE,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """
    macro 平均（先算每個類別的平均、再對類別取平均）的 bootstrap。

    重抽樣必須在「每個類別內部」做，類別的題數才會維持不變；直接對全部題目重抽會
    改變類別組成，算出來的就不是 macro 了。
    """
    groups = [g for g in groups if g]
    if not groups:
        return (0.0, 0.0)
    rng = random.Random(seed)
    macros = []
    for _ in range(iterations):
        macros.append(mean(mean(rng.choices(g, k=len(g))) for g in groups))
    tail = (1.0 - confidence) / 2
    return round(percentile(macros, tail), 4), round(percentile(macros, 1.0 - tail), 4)


def paired_bootstrap_ci(
    pairs: list[tuple[float, float]],
    iterations: int = BOOTSTRAP_ITERATIONS,
    confidence: float = BOOTSTRAP_CONFIDENCE,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float]:
    """
    兩個設定「差值」的信賴區間，逐題配對後重抽樣。

    這比「兩個區間有沒有重疊」嚴謹得多：兩個區間重疊並不代表沒有差異，
    而差值的區間包含 0 才是「這份題庫看不出差異」的正確說法。
    配對（同一題在兩個設定下的分數一起抽）會消掉題目難度造成的變異。
    """
    if not pairs:
        return (0.0, 0.0)
    diffs = [a - b for a, b in pairs]
    return bootstrap_ci(diffs, iterations, confidence, seed)


def score_case(ranked: list[str], relevant: set[str], ks: list[int]) -> dict:
    # @all：以整份回傳清單計分。有 metadata 篩選時檢索會回傳所有符合的文件，
    # 列舉題（一個縣 20 筆）才不會被 top-10 的結構上限壓住
    out = {
        "mrr": reciprocal_rank(ranked, relevant),
        "recall@all": recall_at_k(ranked, relevant, len(ranked)),
        "precision@all": precision_at_k(ranked, relevant, len(ranked)) if ranked else 0.0,
        "r_precision": r_precision(ranked, relevant),
    }
    for k in ks:
        out[f"recall@{k}"] = recall_at_k(ranked, relevant, k)
        out[f"precision@{k}"] = precision_at_k(ranked, relevant, k)
        out[f"hit@{k}"] = hit_at_k(ranked, relevant, k)
        # 跟 recall@k 一起平均，分母一致，所以類別層級的平均天花板直接可比
        out[f"recall_ceiling@{k}"] = recall_ceiling_at_k(relevant, k)
    return out


def aggregate(scored: list[dict]) -> dict:
    if not scored:
        return {}
    keys = scored[0].keys()
    return {key: mean(s[key] for s in scored) for key in keys}
