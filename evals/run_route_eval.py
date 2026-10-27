"""
路由評測：混合路由把每個問句送對了嗎？

系統的賣點之一是「距離查詢直接打 PostGIS、模擬追問直接讀快照、其餘才進 RAG」，
但檢索與生成兩個評測都沒有量過這個判斷本身——而 rag_eval.jsonl 的 130 題裡
只有 4 題含地理關鍵字、0 題含模擬關鍵字，等於 7 條路由有 4 條完全沒被測過。

這支腳本只量路由決策，所以向量庫與資料庫都用假的：
metrics.route() 在 build_context 裡是在呼叫檢索／資料庫「之前」記下的，
因此不需要 Ollama、ChromaDB 或 PostgreSQL，可以在 CI 裡跑。
"""
import argparse
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from models.shelter import NearbyShelter, Shelter  # noqa: E402
from services.chat_service import ChatService  # noqa: E402
from services.knowledge import KnowledgeHit, load_chunks  # noqa: E402
from services.metrics import metrics  # noqa: E402

DEFAULT_CASES = os.path.join(ROOT, "evals", "route_eval.jsonl")

# 模擬進行中的題目要有一份快照才測得到追問路由；內容不影響路由判斷
FAKE_SIMULATION = {
    "type": "earthquake",
    "lat": 23.977,
    "lon": 121.601,
    "radius_km": 10,
    "impacted_count": 1,
    "impacted_shelters": [Shelter("[HUALIEN] 測試避難所", 100, 23.98, 121.60, 10)],
    "population": {},
}


class StubVectorStore:
    """路由評測不看檢索結果，只要 search() 不炸就好"""

    def search(self, query, n_results=None, plan=None):
        return "（路由評測的替身，不是真的檢索結果）"

    def shelter_metadata(self):
        # 「點名的場所存不存在」要對真實名單判斷，否則每個名稱都會被當成不存在；
        # 只讀 data_for_refuge/ 的 JSON，不需要 ChromaDB
        from services.data_fetcher import DataFetcher
        from services.vector_store import VectorStore
        return [VectorStore.build_metadata(s) for s in DataFetcher().get_shelters()]


class StubKnowledge:
    """
    知識路由也只看規則判斷，不需要 embedding。但「知識加避難所」要知識那邊真的有結果才算數，
    所以替身回傳該災害的第一段（距離 0，一定在門檻內）
    """

    def __init__(self):
        self.chunks = {c.id: c for c in load_chunks()}

    def ready(self):
        return True

    def search(self, query, hazards=None, n=3):
        pool = [c for c in self.chunks.values() if not hazards or c.hazard in hazards]
        return [KnowledgeHit(pool[0], 0.0)] if pool else []


class StubRepo:
    def get_nearest_shelters(self, lat, lon, limit=5):
        return [NearbyShelter(Shelter("[HUALIEN] 測試避難所", 100, 23.98, 121.60), distance_km=1.0)]


def load_cases(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def run(cases: list[dict]) -> list[dict]:
    service = ChatService(vector_store=StubVectorStore(), repo=StubRepo(), knowledge=StubKnowledge())
    rows = []
    for case in cases:
        if case.get("simulation"):
            service.set_simulation(FAKE_SIMULATION)
        else:
            service.clear_simulation()
        # 必須先清：某一題若走到沒有呼叫 route() 的路徑，否則會讀到上一題的值
        metrics.clear_route()
        context, early_reply = service.build_context(case["query"])
        rows.append({
            "query": case["query"],
            "simulation": bool(case.get("simulation")),
            "expected": case["expected_route"],
            "actual": metrics.last_route(),
            "answered_without_llm": early_reply is not None,
            "note": case.get("note", ""),
        })
    return rows


def summarize(rows: list[dict]) -> dict:
    routes = sorted({r["expected"] for r in rows} | {r["actual"] for r in rows if r["actual"]})
    matrix = {exp: collections.Counter() for exp in routes}
    for r in rows:
        matrix[r["expected"]][r["actual"]] += 1
    per_route = {}
    for exp in sorted({r["expected"] for r in rows}):
        group = [r for r in rows if r["expected"] == exp]
        hit = sum(1 for r in group if r["actual"] == exp)
        per_route[exp] = {"count": len(group), "correct": hit, "accuracy": round(hit / len(group), 4)}
    correct = sum(1 for r in rows if r["actual"] == r["expected"])
    return {
        "count": len(rows),
        "correct": correct,
        "accuracy": round(correct / len(rows), 4) if rows else 0.0,
        "per_route": per_route,
        "confusion": {exp: dict(counts) for exp, counts in matrix.items()},
        "routes": routes,
    }


def print_report(summary: dict, rows: list[dict]) -> None:
    print(f"\n=== 路由評測：{summary['correct']}/{summary['count']} 正確"
          f"（accuracy {summary['accuracy']:.3f}）===\n")
    print(f"{'expected route':22} {'n':>3} {'correct':>8} {'acc':>7}")
    for route, s in summary["per_route"].items():
        print(f"{route:22} {s['count']:>3} {s['correct']:>8} {s['accuracy']:>7.3f}")

    print("\n混淆矩陣（列＝應該走的路由，欄＝實際走的）")
    routes = summary["routes"]
    print(f"{'':22}" + "".join(f"{r[:10]:>12}" for r in routes))
    for exp in summary["per_route"]:
        cells = "".join(f"{summary['confusion'][exp].get(act, 0):>12}" for act in routes)
        print(f"{exp:22}{cells}")

    wrong = [r for r in rows if r["actual"] != r["expected"]]
    if wrong:
        print(f"\n走錯的 {len(wrong)} 題：")
        for r in wrong:
            sim = "（模擬中）" if r["simulation"] else ""
            print(f"  {r['query']}{sim}")
            print(f"      應該 {r['expected']} → 實際 {r['actual']}")
            if r["note"]:
                print(f"      {r['note']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=DEFAULT_CASES)
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=None,
        help="低於這個正確率就以非 0 結束（給 CI 當回歸哨兵用）",
    )
    args = parser.parse_args()

    cases = load_cases(args.cases)
    rows = run(cases)
    summary = summarize(rows)
    print_report(summary, rows)

    out = args.out or os.path.join(ROOT, "evals", "results", "route_eval.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"\nsaved {out}")

    if args.min_accuracy is not None and summary["accuracy"] < args.min_accuracy:
        print(f"\n路由正確率 {summary['accuracy']:.3f} 低於門檻 {args.min_accuracy}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
