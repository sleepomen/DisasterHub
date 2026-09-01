from statistics import mean


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 1.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def precision_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if k == 0:
        return 0.0
    return len(set(ranked[:k]) & relevant) / k


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


def score_case(ranked: list[str], relevant: set[str], ks: list[int]) -> dict:
    out = {"mrr": reciprocal_rank(ranked, relevant)}
    for k in ks:
        out[f"recall@{k}"] = recall_at_k(ranked, relevant, k)
        out[f"precision@{k}"] = precision_at_k(ranked, relevant, k)
        out[f"hit@{k}"] = hit_at_k(ranked, relevant, k)
    return out


def aggregate(scored: list[dict]) -> dict:
    if not scored:
        return {}
    keys = scored[0].keys()
    return {key: mean(s[key] for s in scored) for key in keys}
