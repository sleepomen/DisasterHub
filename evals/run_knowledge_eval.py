"""
防災知識評測：知識題有沒有走對路、送對段落，回答有沒有忠於官方文件。

題目在 evals/datasets/knowledge_eval.jsonl，分四種：
  positive  知識題，標出應該送出的段落（relevant）與回答必須講到的關鍵事實（facts）
  negative  語料沒有的題目（火災、停班停課、補助）：不能拿知識文件硬答
  mixed     同時問做法與避難所：知識那邊要送對段落
  shelter   帶災害詞但其實在要避難所（「地震了要去哪裡」）：不能被拉去知識路由

第一段只量路由與檢索，不需要 LLM。純知識題的回答就是程式直接給的原文（不經過模型），
所以使用者看到的品質取決於 top1 / precision。--generate 再對混合題真的生成避難所那一半，
並對所有送出知識的題目計分：
  facts      關鍵事實講到幾成（去掉空白後比對）
  num_ok     回答裡的每個數字都在送出的資料裡（條列編號不算）
  novel      找不到出處的句子比例：句子的字元三連組在資料裡出現不到一半就算。
             這是近似判斷，改寫得很兇的正確句子也會被算進來，用來比較版本，不是絕對值
  latin_ok   沒有資料以外的英文（PTWC、PGA 是原文就有的）
  abstained  正例被回成「沒有資料」的比例

混合題的避難所那一半用真實資料（ShelterTextStore：依規則層抽出的鄉鎮直接挑文件，不做向量檢索）。

    python evals/run_knowledge_eval.py
    python evals/run_knowledge_eval.py --generate --label knowledge_llama3.2-3b
"""
import argparse
import json
import os
import re
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402
from evals.lib.gen_metrics import is_abstention  # noqa: E402
from services.chat import query_rules  # noqa: E402
from services.retrieval.knowledge import knowledge_text  # noqa: E402
from services.web.metrics import metrics  # noqa: E402

DEFAULT_CASES = os.path.join(ROOT, "evals", "datasets", "knowledge_eval.jsonl")
RESULTS_DIR = os.path.join(ROOT, "evals", "results")
KNOWLEDGE_ROUTES = {"knowledge", "knowledge_mixed"}
EXPECTED_ROUTE = {"positive": "knowledge", "mixed": "knowledge_mixed"}

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
# 條列編號：「1.」「2、」「(3)」開頭的數字不是內容。
# 模型常把「1. … 2. …」寫在同一行，所以行首或空白後面都算；後面不能再接數字，32.7 這種小數才不會被吃掉
ENUMERATOR_RE = re.compile(r"(?:^|(?<=\s))(?:\(\d+\)|\d+\s*[.、．)）](?!\d))", re.M)
LATIN_RE = re.compile(r"[A-Za-z]{2,}")
SENTENCE_SPLIT = re.compile(r"[。！？；\n]")
# 句子短於這個長度不判斷（「不要。」「立即疏散」這種短句三連組太少，比例沒意義）
MIN_SENTENCE = 8
NOVEL_THRESHOLD = 0.5
# 回答開頭的「依據防災知識資料」「問題：…」這類套話不是內容
BOILERPLATE = ("防災知識資料", "使用者問題", "問題：", "答案：", "回答：")


def load_cases(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def fact_recall(reply: str, facts: list[str]) -> float | None:
    if not facts:
        return None
    body = squash(reply)
    return sum(1 for fact in facts if squash(fact) in body) / len(facts)


def numbers_in(text: str) -> set[str]:
    return set(NUMBER_RE.findall(ENUMERATOR_RE.sub("", text or "")))


def unsupported_numbers(reply: str, context: str) -> list[str]:
    known = set(NUMBER_RE.findall(context or ""))
    return sorted(n for n in numbers_in(reply) if n not in known)


def _trigrams(text: str) -> set[str]:
    text = squash(text)
    return {text[i:i + 3] for i in range(len(text) - 2)}


def novel_sentences(reply: str, context: str) -> tuple[list[str], int]:
    """(找不到出處的句子, 判斷過的句子數)"""
    known = _trigrams(context)
    novel, checked = [], 0
    for raw in SENTENCE_SPLIT.split(ENUMERATOR_RE.sub("", reply or "")):
        sentence = raw.strip(" ：:，,-*■")
        if len(squash(sentence)) < MIN_SENTENCE or any(b in sentence for b in BOILERPLATE):
            continue
        grams = _trigrams(sentence)
        checked += 1
        if sum(1 for g in grams if g in known) / len(grams) < NOVEL_THRESHOLD:
            novel.append(sentence)
    return novel, checked


def foreign_latin(reply: str, context: str) -> list[str]:
    known = set(LATIN_RE.findall(context or ""))
    return sorted({w for w in LATIN_RE.findall(reply or "") if w not in known})


def score_reply(reply: str, context: str, facts: list[str]) -> dict:
    novel, checked = novel_sentences(reply, context)
    numbers = unsupported_numbers(reply, context)
    latin = foreign_latin(reply, context)
    return {
        "facts": fact_recall(reply, facts),
        "num_ok": not numbers,
        "unsupported_numbers": numbers,
        "novel": len(novel) / checked if checked else 0.0,
        "novel_sentences": novel,
        "latin_ok": not latin,
        "latin": latin,
        "abstained": is_abstention(reply),
    }


def build_service(embedder_spec: str):
    import chromadb

    from evals.run_rag_eval import make_embedder
    from evals.run_route_eval import StubRepo
    from services.chat.service import ChatService
    from services.retrieval.knowledge import KnowledgeStore

    knowledge = KnowledgeStore(embedding_function=make_embedder(embedder_spec), client=chromadb.Client())
    t0 = time.perf_counter()
    knowledge.build()
    return ChatService(ShelterTextStore(), StubRepo(), knowledge=knowledge), time.perf_counter() - t0


class ShelterTextStore:
    """
    混合題的避難所那一半用真實資料：依規則層抽出的鄉鎮／地區直接挑出避難所文件，不做向量檢索。
    原本用路由評測的替身（一句「不是真的檢索結果」），實測模型就自己編出「花蓮市民中心 200 人」，
    量到的是替身的問題，不是系統的
    """
    LIMIT = 10

    def __init__(self):
        from services.ingest.fetcher import DataFetcher
        from services.retrieval.vector_store import VectorStore
        self.shelters = DataFetcher().get_shelters()
        self.metadata = [VectorStore.build_metadata(s) for s in self.shelters]
        self.documents = [VectorStore.build_document(s) for s in self.shelters]

    def shelter_metadata(self):
        return self.metadata

    def _pick(self, plan):
        return [
            (doc, meta) for doc, meta in zip(self.documents, self.metadata, strict=True)
            # 沒有地點條件時回前幾間，對應正式服務的語意檢索 top-k
            if plan is None or not (plan.township or plan.region)
            or (plan.township and meta["township"] == plan.township)
            or (not plan.township and plan.region and meta["region"] == plan.region)
        ]

    def search(self, query, n_results=None, plan=None):
        return self.search_result(query, n_results, plan).text

    def search_result(self, query, n_results=None, plan=None):
        """有地點條件時跟正式服務一樣由程式排好清單（services/chat/direct_answer.py），沒有的話交給模型"""
        from services.chat.direct_answer import compose
        from services.retrieval.vector_store import Hit, PlannedResult, SearchResult
        picked = self._pick(plan)
        shown = picked[:self.LIMIT]
        text = "\n".join(f"- {doc}" for doc, _ in shown)
        if plan is None or not (plan.township or plan.region):
            return SearchResult(text)
        hits = [Hit(name=meta["name"], document=doc, distance=0.0, metadata=meta) for doc, meta in shown]
        return SearchResult(text, compose(plan, PlannedResult(hits, total=len(picked), filtered=True)))


def _generate_with_retry(svc, prompt: str, attempts: int = 2) -> dict | None:
    for attempt in range(1, attempts + 1):
        try:
            return svc.generate(prompt)
        except Exception as e:
            print(f"  生成失敗（第 {attempt} 次）：{type(e).__name__}", flush=True)
    return None


def run(cases: list[dict], svc, generate: bool = False) -> list[dict]:
    rows = []
    for i, case in enumerate(cases, 1):
        metrics.clear_route()
        context, early = svc.build_context(case["query"])
        route = metrics.last_route()
        # 純知識題的段落在 early_reply（程式直接回原文），混合題的在 context
        sent = [c.id for c in svc.knowledge_chunks(context or early)]
        # 不經門檻與差距篩選的原始距離：校準 KNOWLEDGE_MAX_DISTANCE 與 FOLLOWER_MARGIN 用
        req = query_rules.knowledge_request(case["query"])
        raw = svc.knowledge.search(req.search_text if req else case["query"], hazards=req.hazards if req else None, n=5)
        relevant = case.get("relevant") or []
        row = {
            "query": case["query"],
            "kind": case["kind"],
            "route": route,
            "early_reply": early,
            "sent": sent,
            "relevant": relevant,
            "distances": [[h.chunk.id, round(h.distance, 3)] for h in raw],
            "note": case.get("note", ""),
        }
        if case["kind"] in EXPECTED_ROUTE:
            row["route_ok"] = route == EXPECTED_ROUTE[case["kind"]]
            row["hit"] = any(c in relevant for c in sent)
            row["top1"] = bool(sent) and sent[0] in relevant
            row["precision"] = sum(1 for c in sent if c in relevant) / len(sent) if sent else 0.0
        elif case["kind"] == "negative":
            # 沒有把知識資料送進 prompt 就算守住；送了的話由 --generate 看模型自己有沒有拒答
            row["held"] = not sent
        else:
            row["route_ok"] = route not in KNOWLEDGE_ROUTES
        if generate and sent and context is None:
            # 純知識題：回答就是程式給的原文，不打模型。分數照算，忠實度依構造是滿分，
            # 這一類真正要看的是上面的 top1 / precision（使用者看到的是不是對的那一段）
            row["reply"] = early
            row["dropped_lines"] = []
            row["latency_s"] = 0.0
            row["scores"] = score_reply(early, early, case.get("facts") or [])
        elif generate and sent:
            # 混合題：程式先送原文，模型只列避難所
            prompt = svc.build_prompt(case["query"], context)
            t0 = time.perf_counter()
            raw_reply = _generate_with_retry(svc, prompt)
            if raw_reply is None:
                # 一題逾時不該讓整輪中止（實測 Ollama 偶爾 120 秒沒有回應）；記下來，不算進分數
                row["error"] = "generation failed twice"
            else:
                generated, dropped = svc.guard_reply((raw_reply.get("response") or "").strip(), prompt)
                reply = f"{knowledge_text(svc.knowledge_chunks(context))}\n\n{generated}"
                row["reply"] = reply
                row["dropped_lines"] = dropped
                row["latency_s"] = round(time.perf_counter() - t0, 1)
                row["scores"] = score_reply(reply, context, case.get("facts") or [])
        print(f"[{i:2d}/{len(cases)}] {case['kind']:8s} {route or '-':16s} {','.join(sent) or '-':50s} {case['query']}", flush=True)
        rows.append(row)
    return rows


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(statistics.mean(values), 3) if values else None


def summarize(rows: list[dict]) -> dict:
    summary = {}
    for kind in ("positive", "mixed"):
        group = [r for r in rows if r["kind"] == kind]
        if group:
            summary[kind] = {
                "n": len(group),
                "route_ok": _mean(float(r["route_ok"]) for r in group),
                "hit": _mean(float(r["hit"]) for r in group),
                "top1": _mean(float(r["top1"]) for r in group),
                "precision": _mean(r["precision"] for r in group),
                "chunks_sent": _mean(len(r["sent"]) for r in group),
            }
    negatives = [r for r in rows if r["kind"] == "negative"]
    if negatives:
        summary["negative"] = {"n": len(negatives), "held": _mean(float(r["held"]) for r in negatives)}
    shelter = [r for r in rows if r["kind"] == "shelter"]
    if shelter:
        summary["shelter"] = {"n": len(shelter), "route_ok": _mean(float(r["route_ok"]) for r in shelter)}

    best = {r["kind"]: [] for r in rows}
    for r in rows:
        if r["distances"]:
            best[r["kind"]].append(r["distances"][0][1])
    summary["best_distance"] = {
        kind: {"max": max(v), "min": min(v)} for kind, v in best.items() if v
    }

    generated = [r for r in rows if "scores" in r]
    if generated:
        answered = [r for r in generated if r["kind"] in EXPECTED_ROUTE]
        summary["generation"] = {
            "n": len(answered),
            "facts": _mean(r["scores"]["facts"] for r in answered),
            "num_ok": _mean(float(r["scores"]["num_ok"]) for r in answered),
            "novel": _mean(r["scores"]["novel"] for r in answered),
            "latin_ok": _mean(float(r["scores"]["latin_ok"]) for r in answered),
            "abstained": _mean(float(r["scores"]["abstained"]) for r in answered),
            "dropped_lines": sum(len(r["dropped_lines"]) for r in answered),
            "latency_p50": _mean([statistics.median(r["latency_s"] for r in answered)]),
            "errors": sum(1 for r in rows if "error" in r),
        }
        leaked = [r for r in generated if r["kind"] == "negative"]
        if leaked:
            summary["generation"]["negative_leaked"] = len(leaked)
            summary["generation"]["negative_abstained"] = _mean(float(r["scores"]["abstained"]) for r in leaked)
    return summary


def print_report(summary: dict, rows: list[dict]) -> None:
    print("\n=== 防災知識評測 ===")
    for kind in ("positive", "mixed"):
        s = summary.get(kind)
        if s:
            print(f"{kind:9s} n={s['n']:2d}  route_ok={s['route_ok']:.3f}  hit={s['hit']:.3f}  "
                  f"top1={s['top1']:.3f}  precision={s['precision']:.3f}  chunks_sent={s['chunks_sent']:.2f}")
    if "negative" in summary:
        print(f"negative  n={summary['negative']['n']:2d}  held={summary['negative']['held']:.3f}")
    if "shelter" in summary:
        print(f"shelter   n={summary['shelter']['n']:2d}  route_ok={summary['shelter']['route_ok']:.3f}")
    print("最近一段的距離（未經門檻）：" + "  ".join(
        f"{k} {v['min']:.3f}～{v['max']:.3f}" for k, v in summary["best_distance"].items()))
    gen = summary.get("generation")
    if gen:
        print(f"generation n={gen['n']}  facts={gen['facts']}  num_ok={gen['num_ok']}  novel={gen['novel']}  "
              f"latin_ok={gen['latin_ok']}  abstained={gen['abstained']}  dropped_lines={gen['dropped_lines']}  "
              f"p50={gen['latency_p50']}s  errors={gen['errors']}")
        if "negative_leaked" in gen:
            print(f"送進模型的負例 {gen['negative_leaked']} 題，模型自己拒答 {gen['negative_abstained']}")
    misses = [r for r in rows if r.get("route_ok") is False or r.get("hit") is False or r.get("held") is False]
    if misses:
        print("\n沒過的題目：")
        for r in misses:
            print(f"  [{r['kind']}] {r['query']}  route={r['route']}  sent={r['sent']}  relevant={r['relevant']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=DEFAULT_CASES)
    parser.add_argument("--embedder", default="ollama:bge-m3")
    parser.add_argument("--generate", action="store_true", help="對送出知識資料的題目真的生成")
    parser.add_argument("--model", default=None, help="覆寫 OLLAMA_MODEL")
    parser.add_argument("--label", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    if args.model:
        config.OLLAMA_MODEL = args.model

    svc, build_s = build_service(args.embedder)
    rows = run(load_cases(args.cases), svc, generate=args.generate)
    summary = summarize(rows)
    print_report(summary, rows)

    label = args.label or ("knowledge_gen" if args.generate else "knowledge_eval")
    out = args.out or os.path.join(RESULTS_DIR, f"{label}.json")
    meta = {
        "label": label, "embedder": args.embedder, "model": config.OLLAMA_MODEL if args.generate else None,
        "knowledge_max_distance": config.KNOWLEDGE_MAX_DISTANCE, "index_build_s": round(build_s, 1),
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "summary": summary, "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
