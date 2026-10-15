"""
邊界題與拒答評測：系統「在哪一層」決定不回答？

RAG_MAX_DISTANCE=0.56 是在 rag_eval.jsonl 的 10 道負例上調出來的，但其中 8 道是被
規則層的範圍外地名擋掉的，也就是距離門檻的經驗基礎只有 2 題。這支腳本拿一組刻意
繞過規則層的邊界題去問：拒答到底是誰做的，還是根本沒有人做。

四層防線（順序與 build_context 一致）：
  rules      query_rules 認出範圍外縣市 → 直接拒答
  off_topic  問的不是避難所（天氣、醫院、補助…）且整句沒有避難主題 → 直接拒答
  distance   純語意檢索的最近距離超過 RAG_MAX_DISTANCE → 回「沒有相關資料」
  llm        以上都沒攔到，資料被送進 prompt，只剩模型自己判斷該不該拒答

重點在於：只要問句裡有範圍內地名或設施關鍵字，規則層就會產生 metadata 篩選，
而距離門檻在有篩選的路徑上**依設計被跳過**（篩選命中本身被當成相關的證據）。
所以「花蓮的天氣」這種問題會直接落到第三層。

--generate 會對落到第三層的題目真的打一次 LLM，看最後防線有沒有守住。
"""
import argparse
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402
from evals.gen_metrics import is_abstention  # noqa: E402
from services import query_rules  # noqa: E402
from services.chat_service import ChatService  # noqa: E402
from services.vector_store import NO_DATA, NO_MATCH, VectorStore  # noqa: E402

DEFAULT_CASES = os.path.join(ROOT, "evals", "boundary_eval.jsonl")
RESULTS_DIR = os.path.join(ROOT, "evals", "results")


def load_cases(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def classify(store: VectorStore, query: str) -> dict:
    """走一次真實的檢索路徑，記下是哪一層做了決定"""
    plan = query_rules.analyze(query)
    if plan.out_of_scope:
        return {"layer": "rules", "refused": True, "filtered": None,
                "top1_distance": None, "hits": 0, "matched_place": plan.out_of_scope}
    # 順序必須跟 build_context 一致，否則「哪一層決定的」會歸因錯誤
    if plan.off_topic:
        return {"layer": "off_topic", "refused": True, "filtered": None,
                "top1_distance": None, "hits": 0, "matched_place": plan.off_topic}

    where = plan.to_where()
    result = store.plan_retrieve(query, plan)
    hits = result.hits
    text = store.search(query, plan=plan)
    refused = text in (NO_DATA, NO_MATCH)
    return {
        "layer": "distance" if refused else "llm",
        "refused": refused,
        "filtered": where is not None,
        "top1_distance": round(hits[0].distance, 4) if hits else None,
        "hits": len(hits),
        "matched_place": plan.describe() or None,
    }


def run(cases: list[dict], store: VectorStore, service: ChatService | None) -> list[dict]:
    rows = []
    for case in cases:
        row = {"query": case["query"], "expect": case["expect"], "kind": case["kind"],
               "note": case.get("note", ""), **classify(store, case["query"])}
        if service is not None and row["layer"] == "llm":
            reply = service.chat(case["query"])
            row["reply"] = reply
            row["llm_abstained"] = is_abstention(reply)
            row["refused"] = bool(row["llm_abstained"])
        rows.append(row)
    return rows


def summarize(rows: list[dict]) -> dict:
    scored = [r for r in rows if r["expect"] in ("refuse", "answer")]
    correct = sum(
        1 for r in scored
        if (r["expect"] == "refuse") == bool(r["refused"])
    )
    by_layer = collections.Counter(r["layer"] for r in rows)
    should_refuse = [r for r in rows if r["expect"] == "refuse"]
    return {
        "count": len(rows),
        "scored": len(scored),
        "correct": correct,
        "accuracy": round(correct / len(scored), 4) if scored else 0.0,
        "decided_by": dict(by_layer),
        "should_refuse": {
            "count": len(should_refuse),
            "by_rules": sum(1 for r in should_refuse if r["layer"] == "rules"),
            "by_off_topic": sum(1 for r in should_refuse if r["layer"] == "off_topic"),
            "by_distance": sum(1 for r in should_refuse if r["layer"] == "distance"),
            "left_to_llm": sum(1 for r in should_refuse if r["layer"] == "llm"),
        },
        "threshold": config.RAG_MAX_DISTANCE,
    }


def print_report(summary: dict, rows: list[dict], generated: bool) -> None:
    s = summary["should_refuse"]
    print(f"\n=== 邊界題與拒答評測（RAG_MAX_DISTANCE={summary['threshold']}）===")
    print(f"該拒答的 {s['count']} 題裡：範圍外規則擋下 {s['by_rules']}、離題規則擋下 {s['by_off_topic']}、"
          f"距離門檻擋下 {s['by_distance']}、落到 LLM {s['left_to_llm']}")
    if generated:
        print(f"計分 {summary['scored']} 題（含 LLM 實際回覆），正確 {summary['correct']}"
              f"（accuracy {summary['accuracy']:.3f}）")
    else:
        print(f"未加 --generate，落到 LLM 的題目一律算成「檢索層沒攔住」："
              f"{summary['correct']}/{summary['scored']}（accuracy {summary['accuracy']:.3f}）")

    print(f"\n{'問句':<22} {'kind':<26} {'期望':<6} {'決定層':<9} {'篩選':<5} {'top1':>7}  結果")
    for r in rows:
        dist = f"{r['top1_distance']:.3f}" if r["top1_distance"] is not None else "-"
        filt = "-" if r["filtered"] is None else ("有" if r["filtered"] else "無")
        if r["expect"] == "refuse_or_answer":
            mark = "（不計分）"
        else:
            mark = "OK" if (r["expect"] == "refuse") == bool(r["refused"]) else "**錯**"
        print(f"{r['query']:<22} {r['kind']:<26} {r['expect']:<6} {r['layer']:<9} {filt:<5} {dist:>7}  {mark}")

    print("\n距離分布（只看沒有 metadata 篩選的題目——距離門檻只在這條路徑上生效）")
    unfiltered = [r for r in rows if r["filtered"] is False and r["top1_distance"] is not None]
    for r in sorted(unfiltered, key=lambda x: x["top1_distance"]):
        side = "該拒答" if r["expect"] == "refuse" else "該回答"
        print(f"  {r['top1_distance']:.3f}  {side}  {r['query']}")
    if not unfiltered:
        print("  （這組題目全部都產生了 metadata 篩選，距離門檻完全沒有作用）")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=DEFAULT_CASES)
    parser.add_argument("--embedder", default="ollama:bge-m3")
    parser.add_argument("--generate", action="store_true", help="對落到 LLM 的題目真的跑一次生成")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    from evals.run_rag_eval import build_store

    cases = load_cases(args.cases)
    store, docs, _ = build_store(args.embedder, "boundary", "current")
    service = ChatService(vector_store=store, repo=None) if args.generate else None
    rows = run(cases, store, service)
    summary = summarize(rows)
    summary["docs"] = docs
    summary["generated"] = bool(args.generate)
    print_report(summary, rows, bool(args.generate))

    out = args.out or os.path.join(RESULTS_DIR, "boundary_eval.json")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"\nsaved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
