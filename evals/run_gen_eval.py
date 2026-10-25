"""
生成端評測：每一題真的走 ChatService（規則層 → 檢索 → 組 prompt → Ollama 生成），
用 evals/gen_metrics.py 的規則對最終回覆評分。

跑完 130 題要半小時到一小時，中途斷電或被中止不該從頭再來，
所以每答完一題就把結果附加寫進 <out>.progress.jsonl 並 fsync 落地，
再跑同一組設定時自動讀回已完成的題目直接略過，最後成功寫出結果才刪掉進度檔。

用法（容器內）：
    python evals/run_gen_eval.py --embedder ollama:bge-m3 --model llama3.2:3b --label llama3.2-3b
    python evals/run_gen_eval.py --embedder ollama:bge-m3 --model qwen2.5:7b --label qwen2.5-7b
    python evals/run_gen_eval.py --limit 10 --category region      # 快速抽查
    python evals/run_gen_eval.py --model qwen2.5:7b --fresh        # 不接續，重頭跑
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
from evals.metrics import (  # noqa: E402
    BOOTSTRAP_ITERATIONS,
    BOOTSTRAP_SEED,
    bootstrap_ci,
    percentile,
    stratified_bootstrap_ci,
)
from evals.run_rag_eval import DEFAULT_CASES, RESULTS_DIR, build_store, load_cases  # noqa: E402


def progress_path_for(out: str) -> str:
    return out + ".progress.jsonl"


def _append_line(handle, obj: dict) -> None:
    """寫一行就落地。斷電時最多丟掉正在寫的那一行，前面的題目都保得住"""
    handle.write(json.dumps(obj, ensure_ascii=False) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def load_progress(path: str, signature: dict, key: str = "query") -> dict:
    """
    讀回上次中斷前已完成的題目，回傳 {row[key]: row}。
    第一行是設定簽章，換模型或改參數就不沿用，避免把不同設定的結果混在一起。
    斷電可能讓最後一行只寫一半，解析不了的行直接丟掉。
    """
    if not os.path.exists(path):
        return {}
    done = {}
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if i == 0:
                if obj.get("signature") != signature:
                    return {}
                continue
            if key in obj:
                done[obj[key]] = obj
    return done


def run(cases, store, shelters, progress_handle=None, done=None):
    from services.chat_service import ChatService

    catalog = build_catalog(shelters)
    svc = ChatService(vector_store=store, repo=None)
    done = done or {}
    rows = []
    total = len(cases)
    for i, c in enumerate(cases, 1):
        cached = done.get(c["query"])
        if cached is not None:
            rows.append(cached)
            print(f"[{i:>3}/{total}] {c['category']:<12}   略過  {c['query']}（已完成）", flush=True)
            continue
        t0 = time.perf_counter()
        context, early = svc.build_context(c["query"])
        meta = {}
        if early:
            reply, generated, context = early, False, ""
        else:
            prompt = svc.build_prompt(c["query"], context)
            raw = svc.generate(prompt)
            # 跟線上一樣過回答防護：拿掉提到 prompt 以外避難所的行
            reply, dropped = svc.guard_reply((raw.get("response") or "").strip(), prompt)
            generated = True
            meta = {
                "dropped_lines": dropped,
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
        row = {
            "query": c["query"],
            "category": c["category"],
            "relevant": c["relevant"],
            "generated": generated,
            "reply": reply,
            "context_chars": len(context),
            "latency_ms": round(latency_ms, 1),
            **meta,
            "scores": scores,
        }
        rows.append(row)
        if progress_handle is not None:
            _append_line(progress_handle, row)
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

        # 95% 信賴區間。120 道正例題下，llama 的 0.961 與 qwen 的 0.971 差 0.01，
        # 沒有區間就無法說這是差異還是雜訊（要比兩個設定請用 evals/compare_runs.py 做配對比較）
        ci_keys = [k for k in CI_KEYS if all(k in r["scores"] for r in positive_rows)]
        per_cat = [
            [g["scores"] for g in by_cat[cat]] for cat in sorted(by_cat) if cat != "negative"
        ]
        summary["ci95"] = {
            "iterations": BOOTSTRAP_ITERATIONS,
            "seed": BOOTSTRAP_SEED,
            "macro": {
                key: stratified_bootstrap_ci([[s[key] for s in group] for group in per_cat])
                for key in ci_keys
            },
            "micro": {
                key: bootstrap_ci([r["scores"][key] for r in positive_rows]) for key in ci_keys
            },
        }
    lat = [r["latency_ms"] for r in rows]
    summary["latency_ms"] = {"p50": round(percentile(lat, 0.5), 1), "p95": round(percentile(lat, 0.95), 1)}
    summary["generated"] = sum(1 for r in rows if r["generated"])
    return summary


# 要報信賴區間的指標：論文表格裡真正被拿來比較的那幾個
CI_KEYS = ["answer_recall", "answer_precision", "complete", "hallucinated", "false_abstain"]

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
        return (
            s["answer_recall"] < 1.0
            or s["hallucinated"]
            or s["false_abstain"]
            or not s["numbers_supported"]
            or not s["format_ok"]
            or s["truncated"]
        )

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


def print_ci(summary) -> None:
    ci = summary.get("ci95") or {}
    if not ci:
        return
    print(f"\n95% 信賴區間（bootstrap {ci['iterations']} 次，seed={ci['seed']}；macro 為類別內分層重抽）")
    for name in ("macro", "micro"):
        for key, (lo, hi) in ci[name].items():
            point = summary[name][key]
            print(f"  {name:<6} {key:<18} {point:.3f}  [{lo:.3f}, {hi:.3f}]  寬度 {hi - lo:.3f}")


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
    parser.add_argument("--fresh", action="store_true", help="不接續上次的進度檔，整組重跑")
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

    out = args.out or os.path.join(RESULTS_DIR, f"{label}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    # 換模型、改生成參數或換題庫都會讓簽章變動，進度檔就不沿用
    signature = {
        "model": config.OLLAMA_MODEL, "embedder": args.embedder,
        "num_predict": config.OLLAMA_NUM_PREDICT, "temperature": config.OLLAMA_TEMPERATURE,
        "num_ctx": config.OLLAMA_NUM_CTX, "cases": args.cases,
        "category": args.category, "limit": args.limit,
    }
    progress_path = progress_path_for(out)
    done = {} if args.fresh else load_progress(progress_path, signature)
    if done:
        print(f"接續上次進度：{len(done)} / {len(cases)} 題已完成（{progress_path}）", flush=True)

    from services.data_fetcher import DataFetcher
    shelters = DataFetcher().get_shelters()
    store, docs, build_s = build_store(args.embedder, label)

    with open(progress_path, "a" if done else "w", encoding="utf-8") as progress_handle:
        if not done:
            _append_line(progress_handle, {"signature": signature})
        rows = run(cases, store, shelters, progress_handle=progress_handle, done=done)

    summary = summarize(rows)
    meta = {
        "label": label, "model": config.OLLAMA_MODEL, "embedder": args.embedder,
        "num_predict": config.OLLAMA_NUM_PREDICT, "temperature": config.OLLAMA_TEMPERATURE,
        "num_ctx": config.OLLAMA_NUM_CTX, "docs": docs, "index_build_s": build_s, "cases": len(cases),
    }
    print_report(summary, meta)
    print_ci(summary)
    print_failures(rows, args.show_failures)

    with open(out, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "summary": summary, "rows": rows}, f, ensure_ascii=False, indent=1)
    print(f"\nsaved {out}")
    # 完整結果已經落地，進度檔功成身退
    os.remove(progress_path)


if __name__ == "__main__":
    main()
