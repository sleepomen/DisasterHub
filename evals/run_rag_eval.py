import argparse
import json
import os
import sys
import time
from statistics import mean

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from evals.metrics import aggregate, percentile, score_case  # noqa: E402

DEFAULT_CASES = os.path.join(ROOT, "evals", "rag_eval.jsonl")
RESULTS_DIR = os.path.join(ROOT, "evals", "results")


def load_cases(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def make_embedder(spec: str):
    from chromadb.utils import embedding_functions
    import config

    if spec == "minilm":
        return embedding_functions.ONNXMiniLM_L6_V2()
    if spec.startswith("ollama:"):
        return embedding_functions.OllamaEmbeddingFunction(
            url=f"{config.OLLAMA_HOST}/api/embeddings",
            model_name=spec.split(":", 1)[1],
        )
    raise SystemExit(f"unknown embedder: {spec}")


def build_store(embedder_spec: str, label: str):
    from services.data_fetcher import DataFetcher
    from services.vector_store import VectorStore

    shelters = DataFetcher().get_shelters()
    if not shelters:
        raise SystemExit("no shelters loaded from data_for_refuge/")
    store = VectorStore(embedding_function=make_embedder(embedder_spec), collection_name=f"eval_{label}")
    t0 = time.perf_counter()
    store.build_index(shelters)
    return store, len(shelters), time.perf_counter() - t0


def run(cases, store, ks, top, threshold):
    rows = []
    for c in cases:
        t0 = time.perf_counter()
        hits = store.retrieve(c["query"], n_results=top)
        latency_ms = (time.perf_counter() - t0) * 1000
        ranked = [h.name for h in hits]
        relevant = set(c["relevant"])
        row = {
            "query": c["query"],
            "category": c["category"],
            "relevant": sorted(relevant),
            "ranked": ranked,
            "distances": [round(h.distance, 4) for h in hits],
            "latency_ms": round(latency_ms, 2),
            "top1_distance": hits[0].distance if hits else None,
        }
        if relevant:
            row["scores"] = score_case(ranked, relevant, ks)
            rel_dists = [h.distance for h in hits if h.name in relevant]
            row["best_relevant_distance"] = min(rel_dists) if rel_dists else None
        else:
            row["false_positive"] = (
                hits[0].distance < threshold if (hits and threshold is not None) else None
            )
        rows.append(row)
    return rows


def summarize(rows, ks):
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)

    summary = {"categories": {}, "negatives": None}
    positive_scores = []
    for cat, group in sorted(by_cat.items()):
        if cat == "negative":
            top1 = [g["top1_distance"] for g in group if g["top1_distance"] is not None]
            fps = [g["false_positive"] for g in group if g["false_positive"] is not None]
            summary["negatives"] = {
                "count": len(group),
                "mean_top1_distance": round(mean(top1), 4) if top1 else None,
                "min_top1_distance": round(min(top1), 4) if top1 else None,
                "false_positive_rate": round(mean(fps), 4) if fps else None,
            }
            continue
        scores = [g["scores"] for g in group]
        positive_scores.extend(scores)
        agg = aggregate(scores)
        best = [g["best_relevant_distance"] for g in group if g.get("best_relevant_distance") is not None]
        summary["categories"][cat] = {
            "count": len(group),
            **{k: round(v, 4) for k, v in agg.items()},
            "mean_best_relevant_distance": round(mean(best), 4) if best else None,
        }

    cat_aggs = [v for v in summary["categories"].values()]
    metric_keys = [f"recall@{k}" for k in ks] + [f"precision@{k}" for k in ks] + [f"hit@{k}" for k in ks] + ["mrr"]
    summary["macro"] = {k: round(mean(c[k] for c in cat_aggs), 4) for k in metric_keys} if cat_aggs else {}
    summary["micro"] = {k: round(v, 4) for k, v in aggregate(positive_scores).items()} if positive_scores else {}
    lat = [r["latency_ms"] for r in rows]
    summary["latency_ms"] = {"p50": round(percentile(lat, 0.5), 2), "p95": round(percentile(lat, 0.95), 2)}
    return summary


def print_report(summary, ks, meta):
    print(f"\n=== RAG eval: {meta['label']}  (embedder={meta['embedder']}, top={meta['top']}, docs={meta['docs']}) ===")
    header = ["category", "n"] + [f"R@{k}" for k in ks] + [f"P@{ks[0]}", "MRR", f"hit@{ks[-1]}", "rel_dist"]
    print(" | ".join(f"{h:<12}" if i == 0 else f"{h:>7}" for i, h in enumerate(header)))
    for cat, s in summary["categories"].items():
        cells = [f"{cat:<12}", f"{s['count']:>7}"]
        cells += [f"{s[f'recall@{k}']:>7.3f}" for k in ks]
        cells += [f"{s[f'precision@{ks[0]}']:>7.3f}", f"{s['mrr']:>7.3f}", f"{s[f'hit@{ks[-1]}']:>7.3f}"]
        cells += [f"{s['mean_best_relevant_distance']:>7.3f}" if s["mean_best_relevant_distance"] is not None else f"{'-':>7}"]
        print(" | ".join(cells))
    for name in ("macro", "micro"):
        s = summary[name]
        if s:
            cells = [f"{name:<12}", f"{'':>7}"]
            cells += [f"{s[f'recall@{k}']:>7.3f}" for k in ks]
            cells += [f"{s[f'precision@{ks[0]}']:>7.3f}", f"{s['mrr']:>7.3f}", f"{s[f'hit@{ks[-1]}']:>7.3f}", f"{'':>7}"]
            print(" | ".join(cells))
    neg = summary["negatives"]
    if neg:
        fp = f"{neg['false_positive_rate']:.3f}" if neg["false_positive_rate"] is not None else "n/a (no --threshold)"
        print(f"negatives    n={neg['count']}  mean_top1_dist={neg['mean_top1_distance']}  min_top1_dist={neg['min_top1_distance']}  FP_rate={fp}")
    lat = summary["latency_ms"]
    print(f"latency      p50={lat['p50']} ms  p95={lat['p95']} ms   index_build={meta['index_build_s']:.2f}s")


def print_misses(rows, k, limit):
    misses = [r for r in rows if r.get("scores") and r["scores"][f"hit@{k}"] == 0]
    if not misses:
        return
    print(f"\n--- {len(misses)} queries with no relevant hit in top-{k} (showing {min(limit, len(misses))}) ---")
    for r in misses[:limit]:
        print(f"[{r['category']}] {r['query']}")
        print(f"   want: {', '.join(r['relevant'][:3])}{' …' if len(r['relevant']) > 3 else ''}")
        print(f"   got : {', '.join(r['ranked'][:3])}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=DEFAULT_CASES)
    parser.add_argument("--embedder", default="minilm", help="minilm | ollama:<model>")
    parser.add_argument("--label", default=None)
    parser.add_argument("--k", default="3,5,10")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--threshold", type=float, default=None, help="cosine distance above which a result is treated as no-match")
    parser.add_argument("--show-misses", type=int, default=15)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    ks = sorted(int(x) for x in args.k.split(","))
    label = args.label or args.embedder.replace(":", "_")
    cases = load_cases(args.cases)
    store, docs, build_s = build_store(args.embedder, label)
    rows = run(cases, store, ks, max(args.top, ks[-1]), args.threshold)
    summary = summarize(rows, ks)
    meta = {"label": label, "embedder": args.embedder, "top": args.top, "docs": docs, "index_build_s": build_s, "threshold": args.threshold, "cases": len(cases)}

    print_report(summary, ks, meta)
    print_misses(rows, ks[-1], args.show_misses)

    out = args.out or os.path.join(RESULTS_DIR, f"{label}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "summary": summary, "rows": rows}, f, ensure_ascii=False, indent=1)
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
