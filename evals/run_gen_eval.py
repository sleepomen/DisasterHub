"""
生成端評測：每一題真的走 ChatService（規則層 → 檢索 → 組 prompt → Ollama 生成），
用 evals/gen_metrics.py 的規則對最終回覆評分。

用法（容器內）：
    python evals/run_gen_eval.py --embedder ollama:bge-m3 --model llama3.2:3b --label llama3.2-3b
    python evals/run_gen_eval.py --embedder ollama:bge-m3 --model qwen2.5:7b --label qwen2.5-7b
    python evals/run_gen_eval.py --limit 10 --category region      # 快速抽查
"""
import argparse
import json
import os
import sys
import time
from statistics import mean

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from evals.gen_metrics import NEGATIVE_KEYS, POSITIVE_KEYS, aggregate, asks_capacity, build_catalog, score_reply  # noqa: E402
from evals.metrics import percentile  # noqa: E402
from evals.run_rag_eval import build_store, load_cases, DEFAULT_CASES, RESULTS_DIR  # noqa: E402


def run(cases, store, shelters, model_label):
    import config
    from services.chat_service import ChatService

    catalog = build_catalog(shelters)
    svc = ChatService(vector_store=store, repo=None)
    rows = []
    total = len(cases)
    for i, c in enumerate(cases, 1):
        t0 = time.perf_counter()
        context, early = svc.build_context(c["query"])
        meta = {}
        if early:
            reply, generated, context = early, False, ""
        else:
            prompt = svc.build_prompt(c["query"], context)
            raw = svc.generate(prompt)
            reply = (raw.get("response") or "").strip()
            generated = True
            meta = {
                "done_reason": raw.get("done_reason"),
                "prompt_tokens": raw.get("prompt_eval_count"),
                "output_tokens": raw.get("eval_count"),
                "generate_ms": round((raw.get("eval_duration") or 0) / 1e6, 1),
            }
        latency_ms = (time.perf_counter() - t0) * 1000
        scores = score_reply(
            reply, c["relevant"], context, catalog,
            expect=c.get("expect"), truncated=(meta.get("done_reason") == "length"),
            query=c["query"], check_capacity=asks_capacity(c["query"], c["category"]),
        )
        rows.append({
            "query": c["query"],
            "category": c["category"],
            "relevant": c["relevant"],
            "generated": generated,
            "reply": reply,
            "context_chars": len(context),
            "latency_ms": round(latency_ms, 1),
            **meta,
            "scores": scores,
        })
        flag = "" if scores.get("answer_recall", scores.get("abstain_correct", 1.0)) >= 1.0 else "  <-- "
        print(f"[{i:>3}/{total}] {c['category']:<12} {latency_ms / 1000:5.1f}s  {c['query']}{flag}", flush=True)
    return rows


def summarize(rows):
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)

    summary = {"categories": {}, "negatives": None}
    positive_rows = []
    for cat, group in sorted(by_cat.items()):
        scores = [g["scores"] for g in group]
        if cat == "negative":
            summary["negatives"] = {"count": len(group), **{k: round(v, 4) for k, v in aggregate(scores, NEGATIVE_KEYS).items()}}
            continue
        positive_rows.extend(group)
        agg = aggregate(scores)
        lat = [g["latency_ms"] for g in group]
        toks = [g["output_tokens"] for g in group if g.get("output_tokens")]
        summary["categories"][cat] = {
            "count": len(group),
            **{k: round(v, 4) for k, v in agg.items()},
            "latency_p50_ms": round(percentile(lat, 0.5), 1),
            "output_tokens_mean": round(mean(toks), 1) if toks else None,
        }

    cats = list(summary["categories"].values())
    if cats:
        summary["macro"] = {
            k: round(mean(c[k] for c in cats if k in c), 4)
            for k in POSITIVE_KEYS if any(k in c for c in cats)
        }
        summary["micro"] = {k: round(v, 4) for k, v in aggregate([r["scores"] for r in positive_rows]).items()}
    lat = [r["latency_ms"] for r in rows]
    summary["latency_ms"] = {"p50": round(percentile(lat, 0.5), 1), "p95": round(percentile(lat, 0.95), 1)}
    summary["generated"] = sum(1 for r in rows if r["generated"])
    return summary


COLUMNS = [
    ("answer_recall", "ansR"), ("answer_precision", "ansP"), ("complete", "full"),
    ("hallucinated", "halluc"), ("false_abstain", "f_abst"), ("numbers_supported", "num_ok"),
    ("capacity_stated", "cap_ok"), ("top1_correct", "top1"), ("format_ok", "fmt_ok"), ("truncated", "trunc"),
]


def print_report(summary, meta):
    print(f"\n=== Generation eval: {meta['label']}  (model={meta['model']}, embedder={meta['embedder']}, "
          f"num_predict={meta['num_predict']}, temp={meta['temperature']}, cases={meta['cases']}, generated={summary['generated']}) ===")
    header = ["category", "n"] + [short for _, short in COLUMNS] + ["p50 s", "tokens"]
    print(" | ".join(f"{h:<12}" if i == 0 else f"{h:>6}" for i, h in enumerate(header)))

    def line(name, s):
        cells = [f"{name:<12}", f"{s.get('count', ''):>6}"]
        for key, _ in COLUMNS:
            cells.append(f"{s[key]:>6.3f}" if key in s else f"{'-':>6}")
        cells.append(f"{s['latency_p50_ms'] / 1000:>6.1f}" if "latency_p50_ms" in s else f"{'':>6}")
        cells.append(f"{s['output_tokens_mean']:>6.0f}" if s.get("output_tokens_mean") else f"{'':>6}")
        print(" | ".join(cells))

    for cat, s in summary["categories"].items():
        line(cat, s)
    for name in ("macro", "micro"):
        if summary.get(name):
            line(name, summary[name])
    neg = summary["negatives"]
    if neg:
        print(f"negatives    n={neg['count']}  abstain_correct={neg['abstain_correct']:.3f}  "
              f"format_ok={neg['format_ok']:.3f}  truncated={neg['truncated']:.3f}")
    lat = summary["latency_ms"]
    print(f"latency      p50={lat['p50'] / 1000:.1f}s  p95={lat['p95'] / 1000:.1f}s   index_build={meta['index_build_s']:.2f}s")


def print_failures(rows, limit):
    def bad(r):
        s = r["scores"]
        if "abstain_correct" in s:
            return s["abstain_correct"] < 1.0
        return s["answer_recall"] < 1.0 or s["hallucinated"] or s["false_abstain"] or not s["numbers_supported"] or not s["format_ok"] or s["truncated"]

    failures = [r for r in rows if bad(r)]
    if not failures:
        return
    print(f"\n--- {len(failures)} replies with a problem (showing {min(limit, len(failures))}) ---")
    for r in failures[:limit]:
        s = r["scores"]
        problems = []
        if "abstain_correct" in s:
            problems.append("should_abstain")
        else:
            if s["answer_recall"] < 1.0:
                missing = [n for n in r["relevant"] if n not in s["mentioned"]]
                problems.append(f"recall={s['answer_recall']:.2f} missing={len(missing)}")
            if s["unsupported_mentions"]:
                problems.append(f"unsupported={s['unsupported_mentions'][:2]}")
            if s["false_abstain"]:
                problems.append("false_abstain")
            if s["unsupported_numbers"]:
                problems.append(f"numbers={s['unsupported_numbers'][:4]}")
        if s["invented"]:
            problems.append(f"invented={s['invented'][:2]}")
        if s["format_issues"]:
            problems.append(f"format={s['format_issues']}")
        if s["truncated"]:
            problems.append("truncated")
        print(f"[{r['category']}] {r['query']}   ({'; '.join(problems)})")
        snippet = r["reply"].replace("\n", " ")
        print(f"   reply: {snippet[:160]}{'…' if len(snippet) > 160 else ''}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=DEFAULT_CASES)
    parser.add_argument("--embedder", default="ollama:bge-m3", help="minilm | ollama:<model>")
    parser.add_argument("--model", default=None, help="覆寫 OLLAMA_MODEL")
    parser.add_argument("--num-predict", type=int, default=None, help="覆寫 OLLAMA_NUM_PREDICT")
    parser.add_argument("--temperature", type=float, default=None, help="覆寫 OLLAMA_TEMPERATURE")
    parser.add_argument("--label", default=None)
    parser.add_argument("--category", default=None, help="只跑某個類別")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 題（抽查用）")
    parser.add_argument("--show-failures", type=int, default=20)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    import config
    if args.model:
        config.OLLAMA_MODEL = args.model
    if args.num_predict is not None:
        config.OLLAMA_NUM_PREDICT = args.num_predict
    if args.temperature is not None:
        config.OLLAMA_TEMPERATURE = args.temperature

    cases = load_cases(args.cases)
    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
    if args.limit:
        cases = cases[: args.limit]
    label = args.label or f"gen_{config.OLLAMA_MODEL.replace(':', '-')}"

    from services.data_fetcher import DataFetcher
    shelters = DataFetcher().get_shelters()
    store, docs, build_s = build_store(args.embedder, label)

    rows = run(cases, store, shelters, config.OLLAMA_MODEL)
    summary = summarize(rows)
    meta = {
        "label": label, "model": config.OLLAMA_MODEL, "embedder": args.embedder,
        "num_predict": config.OLLAMA_NUM_PREDICT, "temperature": config.OLLAMA_TEMPERATURE,
        "num_ctx": config.OLLAMA_NUM_CTX, "docs": docs, "index_build_s": build_s, "cases": len(cases),
    }
    print_report(summary, meta)
    print_failures(rows, args.show_failures)

    out = args.out or os.path.join(RESULTS_DIR, f"{label}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "summary": summary, "rows": rows}, f, ensure_ascii=False, indent=1)
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
