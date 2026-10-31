"""
兩個評測結果的配對比較。

為什麼不是「看兩個信賴區間有沒有重疊」：兩個區間重疊**不代表**沒有差異，
那是常見的誤讀。要回答「這個差異有意義嗎」，看的是「差值」的信賴區間有沒有包含 0。
而且配對（同一題在兩個設定下的分數綁在一起重抽）會消掉題目難度造成的變異，
所以比兩個各自獨立的區間敏感得多——130 題的規模下這個差別很關鍵。

檢索結果與生成結果都適用：兩邊的 rows 都是逐題的 {"query": ..., "scores": {...}}。

    python evals/lib/compare_runs.py evals/results/gen_qwen2.5-7b.json evals/results/gen_llama3.2-3b.json
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from evals.lib.metrics import (  # noqa: E402
    BOOTSTRAP_ITERATIONS,
    BOOTSTRAP_SEED,
    paired_bootstrap_ci,
)

# 沒有指定 --keys 時要比較的指標，檢索與生成的常用鍵都放進來，有就比
DEFAULT_KEYS = [
    "answer_recall", "answer_precision", "complete", "hallucinated", "false_abstain",
    "recall@3", "recall@10", "recall@all", "precision@all", "r_precision", "mrr",
]


def load_rows(path: str) -> dict[str, dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rows = data.get("rows") or []
    by_query: dict[str, dict] = {}
    for r in rows:
        query = r["query"]
        if query in by_query:
            raise SystemExit(
                f"{os.path.basename(path)} 裡有重複的題目「{query}」，無法逐題配對；"
                "請先確認評測集沒有重複題目"
            )
        by_query[query] = r.get("scores") or {}
    if not by_query:
        raise SystemExit(f"{path} 沒有逐題結果（rows），無法配對比較")
    return by_query


def compare(a_rows, b_rows, keys, iterations, seed):
    shared = [q for q in a_rows if q in b_rows]
    out = {"shared": len(shared), "only_in_a": len(a_rows) - len(shared), "only_in_b": len(b_rows) - len(shared)}
    metrics_out = {}
    for key in keys:
        pairs = [
            (a_rows[q][key], b_rows[q][key])
            for q in shared
            if key in a_rows[q] and key in b_rows[q]
        ]
        if not pairs:
            continue
        mean_a = sum(p[0] for p in pairs) / len(pairs)
        mean_b = sum(p[1] for p in pairs) / len(pairs)
        lo, hi = paired_bootstrap_ci(pairs, iterations=iterations, seed=seed)
        metrics_out[key] = {
            "n": len(pairs),
            "a": round(mean_a, 4),
            "b": round(mean_b, 4),
            "diff": round(mean_a - mean_b, 4),
            "ci95": [lo, hi],
            # 區間包含 0 就是「這份題庫看不出差異」，不是「兩者相同」
            "significant": not (lo <= 0.0 <= hi),
        }
    out["metrics"] = metrics_out
    return out


def print_report(result, label_a, label_b, iterations, seed):
    print(f"\n=== 配對比較：A={label_a}  B={label_b} ===")
    print(f"共用題目 {result['shared']} 題"
          f"（只在 A 的 {result['only_in_a']} 題、只在 B 的 {result['only_in_b']} 題已排除）")
    print(f"bootstrap {iterations} 次，seed={seed}，差值為 A − B\n")
    print(f"{'metric':<18} {'n':>4} {'A':>7} {'B':>7} {'A−B':>8}   {'95% CI of A−B':<22} 結論")
    for key, m in result["metrics"].items():
        lo, hi = m["ci95"]
        verdict = "有差異" if m["significant"] else "看不出差異（區間含 0）"
        print(f"{key:<18} {m['n']:>4} {m['a']:>7.3f} {m['b']:>7.3f} {m['diff']:>+8.3f}   "
              f"[{lo:>+.3f}, {hi:>+.3f}]{'':<4} {verdict}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("a", help="結果檔 A（差值為 A − B）")
    parser.add_argument("b", help="結果檔 B")
    parser.add_argument("--keys", default=None, help="逗號分隔；預設比較常用指標")
    parser.add_argument("--iterations", type=int, default=BOOTSTRAP_ITERATIONS)
    parser.add_argument("--seed", type=int, default=BOOTSTRAP_SEED)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    keys = [k.strip() for k in args.keys.split(",")] if args.keys else DEFAULT_KEYS
    a_rows, b_rows = load_rows(args.a), load_rows(args.b)
    result = compare(a_rows, b_rows, keys, args.iterations, args.seed)

    label_a = os.path.basename(args.a)[:-5]
    label_b = os.path.basename(args.b)[:-5]
    print_report(result, label_a, label_b, args.iterations, args.seed)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"a": label_a, "b": label_b, "iterations": args.iterations,
                       "seed": args.seed, **result}, f, ensure_ascii=False, indent=2)
        print(f"\nsaved {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
