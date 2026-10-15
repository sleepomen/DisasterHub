"""
反事實上下文忠實度評測（改資料的部分與評分在 evals/faithfulness.py）。

題目取 rag_eval.jsonl 裡只有一個正確答案的正例（64 題），每題做兩種改動：
  swap_number  真實檢索結果裡，目標那一行的容量換成假值 → 模型該講假值
  drop_target  純語意檢索結果去掉目標那間 → 模型該說找不到，不能硬湊

prompt 用 ChatService.build_prompt 組，跟線上一模一樣，只有【避難所資料】被換掉。
中斷後接續的機制與 run_gen_eval.py 相同（<out>.progress.jsonl）。

用法（容器內）：
    python evals/run_faithfulness_eval.py --model qwen2.5:7b --label qwen2.5-7b
    python evals/run_faithfulness_eval.py --kinds swap_number --limit 5     # 快速抽查
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from evals.faithfulness import KINDS, SCORE_KEYS, drop_target, score_drop, score_swap, swap_number  # noqa: E402
from evals.gen_metrics import aggregate, build_catalog  # noqa: E402
from evals.metrics import BOOTSTRAP_ITERATIONS, BOOTSTRAP_SEED, bootstrap_ci  # noqa: E402
from evals.run_gen_eval import _append_line, load_progress, progress_path_for  # noqa: E402
from evals.run_rag_eval import DEFAULT_CASES, RESULTS_DIR, build_store, load_cases  # noqa: E402
from services.shelter_profile import strip_region_tag  # noqa: E402


def single_answer_cases(cases: list[dict]) -> list[dict]:
    """只有一個正確答案的題目，「目標那一行」才有明確定義"""
    return [c for c in cases if len(c["relevant"]) == 1]


def prepare(kind: str, case: dict, svc, store, capacities: dict) -> tuple[str | None, dict]:
    """回傳 (改過的 context, 額外資訊)；這題做不了這種改動時 context 為 None，附上原因"""
    (target,) = case["relevant"]
    if kind == "swap_number":
        context, early = svc.build_context(case["query"])
        if early:
            return None, {"skip": "early_reply"}
        perturbed = swap_number(context, strip_region_tag(target), case["query"])
        if perturbed is None:
            return None, {"skip": "target_not_in_context"}
        return perturbed["context"], {"perturbed": perturbed}
    if kind == "drop_target":
        import config
        hits = store.retrieve(case["query"], n_results=config.RAG_TOP_K + 1)
        context = drop_target(hits, target, config.RAG_TOP_K)
        if context is None:
            return None, {"skip": "nothing_left"}
        return context, {"target_capacity": capacities.get(target)}
    raise ValueError(kind)


def run(cases, kinds, svc, store, catalog, capacities, progress_handle=None, done=None):
    done = done or {}
    rows = []
    jobs = [(kind, c) for kind in kinds for c in cases]
    for i, (kind, c) in enumerate(jobs, 1):
        key = f"{kind}|{c['query']}"
        prefix = f"[{i:>3}/{len(jobs)}] {kind:<12} {c['category']:<12}"
        if key in done:
            rows.append(done[key])
            print(f"{prefix}   略過  {c['query']}（已完成）", flush=True)
            continue

        t0 = time.perf_counter()
        context, info = prepare(kind, c, svc, store, capacities)
        row = {"key": key, "kind": kind, "query": c["query"], "category": c["category"], "target": c["relevant"][0]}
        if context is None:
            row.update(skipped=info["skip"])
        else:
            raw = svc.generate(svc.build_prompt(c["query"], context))
            reply = (raw.get("response") or "").strip()
            if kind == "swap_number":
                scores = score_swap(reply, info["perturbed"], c["query"])
                row["original"] = info["perturbed"]["original"]
                row["counterfactual"] = info["perturbed"]["counterfactual"]
            else:
                scores = score_drop(reply, context, c["query"], c["relevant"][0], info["target_capacity"], catalog)
            row.update(
                reply=reply,
                context=context,
                truncated=raw.get("done_reason") == "length",
                latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                scores=scores,
            )
        rows.append(row)
        if progress_handle is not None:
            _append_line(progress_handle, row)
        if "skipped" in row:
            print(f"{prefix}   跳過  {c['query']}（{row['skipped']}）", flush=True)
        else:
            flag = "" if row["scores"]["faithful"] else "  <-- "
            print(f"{prefix} {row['latency_ms'] / 1000:5.1f}s  {c['query']}{flag}", flush=True)
    return rows


def summarize(rows, kinds):
    summary = {}
    for kind in kinds:
        group = [r for r in rows if r["kind"] == kind]
        scored = [r for r in group if "scores" in r]
        keys = SCORE_KEYS[kind]
        by_cat = {}
        for r in scored:
            by_cat.setdefault(r["category"], []).append(r["scores"])
        summary[kind] = {
            "count": len(scored),
            "skipped": len(group) - len(scored),
            **{k: round(v, 4) for k, v in aggregate([r["scores"] for r in scored], keys).items()},
            "faithful_ci95": bootstrap_ci([r["scores"]["faithful"] for r in scored]) if scored else None,
            "truncated": sum(1 for r in scored if r["truncated"]),
            "categories": {
                cat: {"count": len(s), **{k: round(v, 4) for k, v in aggregate(s, keys).items()}}
                for cat, s in sorted(by_cat.items())
            },
        }
    summary["bootstrap"] = {"iterations": BOOTSTRAP_ITERATIONS, "seed": BOOTSTRAP_SEED}
    return summary


def print_report(summary, rows, meta, limit):
    print(f"\n=== 反事實忠實度：{meta['label']}（model={meta['model']}, temp={meta['temperature']}）===")
    for kind in meta["kinds"]:
        s = summary[kind]
        lo, hi = s["faithful_ci95"] or (0.0, 0.0)
        print(f"\n[{kind}] 計分 {s['count']} 題、跳過 {s['skipped']} 題、截斷 {s['truncated']} 題")
        print(f"  faithful {s.get('faithful', 0):.3f}  95% CI [{lo:.3f}, {hi:.3f}]")
        for key in SCORE_KEYS[kind][1:]:
            if key in s:
                print(f"  {key:<19}{s[key]:.3f}")
        print(f"  {'category':<14}{'n':>4}  " + "  ".join(f"{k[:10]:>10}" for k in SCORE_KEYS[kind]))
        for cat, c in s["categories"].items():
            print(f"  {cat:<14}{c['count']:>4}  " + "  ".join(f"{c.get(k, 0):>10.3f}" for k in SCORE_KEYS[kind]))

    bad = [r for r in rows if "scores" in r and not r["scores"]["faithful"]]
    if bad:
        print(f"\n--- 不忠實的回覆 {len(bad)} 則（列出 {min(limit, len(bad))}）---")
        for r in bad[:limit]:
            s = r["scores"]
            if r["kind"] == "swap_number":
                why = f"資料寫 {r['counterfactual']['capacity']}、原值 {r['original']['capacity']}；" \
                      f"new={s['new_stated']:.0f} old={s['old_stated']:.0f}"
            else:
                why = f"mentioned={s['target_mentioned']:.0f} ack={s['acknowledged']:.0f} " \
                      f"substituted={s['substituted']:.0f} numbers={s['unsupported_numbers'][:4]}"
            snippet = r["reply"].replace("\n", " ")
            print(f"[{r['kind']}] {r['query']}  ({why})")
            print(f"   reply: {snippet[:160]}{'…' if len(snippet) > 160 else ''}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=DEFAULT_CASES)
    parser.add_argument("--embedder", default="ollama:bge-m3")
    parser.add_argument("--model", default=None, help="覆寫 OLLAMA_MODEL")
    parser.add_argument("--temperature", type=float, default=None, help="覆寫 OLLAMA_TEMPERATURE")
    parser.add_argument("--kinds", nargs="+", default=list(KINDS), choices=KINDS)
    parser.add_argument("--label", default=None)
    parser.add_argument("--limit", type=int, default=None, help="每種改動只跑前 N 題（抽查用）")
    parser.add_argument("--show-failures", type=int, default=20)
    parser.add_argument("--out", default=None)
    parser.add_argument("--fresh", action="store_true", help="不接續上次的進度檔，整組重跑")
    args = parser.parse_args()

    import config
    if args.model:
        config.OLLAMA_MODEL = args.model
    if args.temperature is not None:
        config.OLLAMA_TEMPERATURE = args.temperature

    cases = single_answer_cases(load_cases(args.cases))
    if args.limit:
        cases = cases[: args.limit]
    label = args.label or config.OLLAMA_MODEL.replace(":", "-")
    out = args.out or os.path.join(RESULTS_DIR, f"faithfulness_{label}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    signature = {
        "model": config.OLLAMA_MODEL, "embedder": args.embedder,
        "num_predict": config.OLLAMA_NUM_PREDICT, "temperature": config.OLLAMA_TEMPERATURE,
        "num_ctx": config.OLLAMA_NUM_CTX, "cases": args.cases, "kinds": args.kinds, "limit": args.limit,
    }
    progress_path = progress_path_for(out)
    done = {} if args.fresh else load_progress(progress_path, signature, key="key")
    if done:
        print(f"接續上次進度：{len(done)} 筆已完成（{progress_path}）", flush=True)

    from services.chat_service import ChatService
    from services.data_fetcher import DataFetcher
    shelters = DataFetcher().get_shelters()
    catalog = build_catalog(shelters)
    capacities = {s.name: s.capacity for s in shelters}
    store, docs, _ = build_store(args.embedder, f"faith_{label}")
    svc = ChatService(vector_store=store, repo=None)

    with open(progress_path, "a" if done else "w", encoding="utf-8") as progress_handle:
        if not done:
            _append_line(progress_handle, {"signature": signature})
        rows = run(cases, args.kinds, svc, store, catalog, capacities, progress_handle, done)

    summary = summarize(rows, args.kinds)
    meta = {
        "label": label, "model": config.OLLAMA_MODEL, "embedder": args.embedder,
        "num_predict": config.OLLAMA_NUM_PREDICT, "temperature": config.OLLAMA_TEMPERATURE,
        "num_ctx": config.OLLAMA_NUM_CTX, "docs": docs, "cases": len(cases), "kinds": args.kinds,
    }
    print_report(summary, rows, meta, args.show_failures)

    with open(out, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "summary": summary, "rows": rows}, f, ensure_ascii=False, indent=1)
    print(f"\nsaved {out}")
    os.remove(progress_path)


if __name__ == "__main__":
    main()
