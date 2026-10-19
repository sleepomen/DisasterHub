"""
邊界題與拒答評測：系統「在哪一層」決定不回答？

RAG_MAX_DISTANCE=0.56 是在 rag_eval.jsonl 的 10 道負例上調出來的，但其中 8 道是被
規則層的範圍外地名擋掉的，也就是距離門檻的經驗基礎只有 2 題。這支腳本拿一組刻意
繞過規則層的邊界題去問：拒答到底是誰做的，還是根本沒有人做。

六層防線（順序與 build_context 一致）：
  rules         query_rules 認出範圍外縣市 → 直接拒答
  off_topic     問的不是避難所（天氣、醫院、補助…）且整句沒有避難主題 → 直接拒答
  unknown_name  點名了資料裡沒有的場所（頭城國中、花蓮縣立游泳池）→ 程式直接回「查無」並推薦名稱相近的
  no_clue       沒有任何篩選條件，也沒有任何避難線索（借錢、洗衣服）→ 直接回「沒有相關資料」
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
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402
from evals.faithfulness import MISSING_RE  # noqa: E402
from evals.gen_metrics import (  # noqa: E402
    MIN_CHECKED_NUMBER,
    build_catalog,
    integers_in,
    is_abstention,
    mentioned_shelters,
)
from services import query_rules  # noqa: E402
from services.chat_service import ChatService, known_names_of  # noqa: E402
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

    names = known_names_of(store.shelter_metadata())
    unknown = query_rules.unknown_shelter_name(query, names)
    if unknown:
        return {"layer": "unknown_name", "refused": True, "filtered": None,
                "top1_distance": None, "hits": 0, "matched_place": unknown}
    if not (plan.has_filter or plan.order_by_capacity) and names and not query_rules.has_shelter_clue(query, names):
        return {"layer": "no_clue", "refused": True, "filtered": False,
                "top1_distance": None, "hits": 0, "matched_place": None}

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


def is_nonexistent(row: dict) -> bool:
    return row["kind"].startswith("nonexistent")


# 一句話裡出現這些，就是在描述那個場所的狀態，而不是在說「沒有這間」
DETAIL_RE = re.compile(r"容量|空位|收容|地址|位於|\d")
NO_SPACE_RE = re.compile(r"(?:沒有|無|尚無)(?:剩餘)?空位")


def judge_nonexistent(reply: str, name: str, catalog) -> dict:
    """
    問的場所不在資料裡時，「對」的定義跟一般拒答不同：先交代沒有這一間，之後再推薦別間也可以。
    錯的有三種，嚴重度由高到低：
      fabricated   替不存在的那間講出容量、空位、地址——「頭城國中：容量 400 人」其實是頭城國小的資料。
                   以句為單位判斷，「…容量 400 人。目前沒有完全符合的避難所」前一句已經編了，後一句救不回來
      substituted  沒交代就直接拿別間來答，使用者會以為那就是答案
      其他         「X：無」這種沒頭沒尾的回覆，沒編也沒換，但也沒告訴使用者資料裡沒有
    反事實評測（evals/faithfulness.py）量到 qwen2.5:7b 在 57.8% 的漏撈題裡默默換答，
    這裡驗證線上路徑是不是也一樣。
    """
    # 「沒有空位」是在講狀態不是在說沒有這間，先換掉，否則 MISSING_RE 的「沒有」會把它當成交代
    def says_missing(text: str) -> bool:
        return bool(MISSING_RE.search(NO_SPACE_RE.sub("空位", text)))

    sentences = [s for s in re.split(r"[\n。]", reply) if name in s]
    fabricated = any(DETAIL_RE.search(s) and not says_missing(s) for s in sentences)
    acknowledged = is_abstention(reply) or says_missing(reply)
    mentioned = mentioned_shelters(reply, catalog)
    substituted = bool(mentioned) and not acknowledged and not fabricated
    if fabricated:
        verdict = "fabricated"
    elif substituted:
        verdict = "substituted"
    elif acknowledged:
        verdict = "acknowledged"
    else:
        verdict = "other"
    return {
        "verdict": verdict,
        "acknowledged": acknowledged,
        "fabricated": fabricated,
        "substituted": substituted,
        "mentioned": mentioned,
    }


def judge_named_answer(reply: str, shelter, catalog) -> dict:
    """
    點名某一間（含錯字）的題目：回覆最先講到的必須是那一間，而且回覆裡的數字只能是它的容量或剩餘空位。
    只看「有沒有拒答」的話，把涙強國小答成 250 人（實際 300）也會算對——那正是錯字題實際出過的錯
    """
    mentioned = mentioned_shelters(reply, catalog)
    allowed = {shelter.capacity, shelter.remaining}
    numbers = sorted(n for n in integers_in(reply) if n >= MIN_CHECKED_NUMBER)
    wrong_numbers = [n for n in numbers if n not in allowed]
    named_ok = bool(mentioned) and mentioned[0] == shelter.name
    return {
        "target": shelter.name,
        "named_ok": named_ok,
        "wrong_numbers": wrong_numbers,
        "answer_ok": named_ok and not wrong_numbers,
    }


def row_correct(row: dict) -> bool:
    if (row["expect"] == "refuse") != bool(row["refused"]):
        return False
    # 該回答的題目，沒拒答還不夠：點名題要答對那一間、數字要對
    return row.get("answer_ok", True)


def run(cases: list[dict], store: VectorStore, service: ChatService | None, catalog=None,
        shelters: dict | None = None) -> list[dict]:
    rows = []
    for case in cases:
        row = {"query": case["query"], "expect": case["expect"], "kind": case["kind"],
               "note": case.get("note", ""), **classify(store, case["query"])}
        # 不存在的場所即使被 unknown_name 層攔下也要看實際回覆：固定回覆本身也得通過同一套判定
        if service is not None and (row["layer"] == "llm" or is_nonexistent(row)):
            reply = service.chat(case["query"])
            row["reply"] = reply
            row["llm_abstained"] = is_abstention(reply) if row["layer"] == "llm" else None
            if row["layer"] == "llm":
                row["refused"] = bool(row["llm_abstained"])
            if is_nonexistent(row) and catalog is not None:
                row.update(judge_nonexistent(reply, case["name"], catalog))
                row["refused"] = row["verdict"] == "acknowledged"
                # 記下模型看到的標頭，才分得出是走了篩選、點名還是純語意路徑
                context, _ = service.build_context(case["query"])
                row["context_header"] = (context or "").split("\n", 1)[0]
            if case.get("shelter") and catalog is not None and shelters is not None:
                row.update(judge_named_answer(reply, shelters[case["shelter"]], catalog))
        rows.append(row)
    return rows


def summarize(rows: list[dict]) -> dict:
    scored = [r for r in rows if r["expect"] in ("refuse", "answer")]
    correct = sum(1 for r in scored if row_correct(r))
    by_layer = collections.Counter(r["layer"] for r in rows)
    should_refuse = [r for r in rows if r["expect"] == "refuse"]
    judged = [r for r in rows if "verdict" in r]
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
            "by_unknown_name": sum(1 for r in should_refuse if r["layer"] == "unknown_name"),
            "by_no_clue": sum(1 for r in should_refuse if r["layer"] == "no_clue"),
            "by_distance": sum(1 for r in should_refuse if r["layer"] == "distance"),
            "left_to_llm": sum(1 for r in should_refuse if r["layer"] == "llm"),
        },
        "threshold": config.RAG_MAX_DISTANCE,
        "nonexistent": {
            "count": len(judged),
            **{v: sum(1 for r in judged if r["verdict"] == v)
               for v in ("acknowledged", "fabricated", "substituted", "other")},
        },
    }


def print_report(summary: dict, rows: list[dict], generated: bool) -> None:
    s = summary["should_refuse"]
    print(f"\n=== 邊界題與拒答評測（RAG_MAX_DISTANCE={summary['threshold']}）===")
    print(f"該拒答的 {s['count']} 題裡：範圍外規則擋下 {s['by_rules']}、離題規則擋下 {s['by_off_topic']}、"
          f"不存在場所規則擋下 {s['by_unknown_name']}、無線索規則擋下 {s['by_no_clue']}、"
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
            mark = "OK" if row_correct(r) else "**錯**"
        print(f"{r['query']:<22} {r['kind']:<26} {r['expect']:<6} {r['layer']:<9} {filt:<5} {dist:>7}  {mark}")

    named = [r for r in rows if "answer_ok" in r]
    if named:
        print(f"\n點名題（含錯字）{len(named)} 題：答對那一間而且數字正確 {sum(r['answer_ok'] for r in named)}")
        for r in named:
            verdict = "對" if r["answer_ok"] else ("講錯間" if not r["named_ok"] else f"數字錯 {r['wrong_numbers']}")
            reply = r["reply"].replace("\n", " ")
            print(f"  [{verdict}] {r['query']}")
            print(f"     回覆：{reply[:90]}{'…' if len(reply) > 90 else ''}")

    judged = [r for r in rows if "verdict" in r]
    if judged:
        n = summary["nonexistent"]
        print(f"\n資料裡不存在的場所 {n['count']} 題：有交代沒有這間 {n['acknowledged']}、"
              f"替它編出資料 {n['fabricated']}、沒交代就拿別間來答 {n['substituted']}、其他 {n['other']}")
        labels = {"acknowledged": "交代", "fabricated": "編造", "substituted": "換答", "other": "其他"}
        for r in judged:
            verdict = labels[r["verdict"]]
            reply = r["reply"].replace("\n", " ")
            print(f"  [{verdict}] {r['query']}")
            print(f"     標頭：{r['context_header'][:70]}")
            print(f"     回覆：{reply[:90]}{'…' if len(reply) > 90 else ''}")

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
    from services.data_fetcher import DataFetcher
    all_shelters = DataFetcher().get_shelters()
    catalog = build_catalog(all_shelters)
    rows = run(cases, store, service, catalog, {s.name: s for s in all_shelters})
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
