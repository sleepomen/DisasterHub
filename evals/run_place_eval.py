"""
小地名評測：問「知本附近有避難所嗎」「七星潭最近的避難所在哪」這類不是鄉鎮名的地名時，
回答（或交給模型的資料）裡的避難所是不是真的在那附近。

小地名不在規則層的鄉鎮清單裡，以前只能靠語意檢索猜：「知本附近」撈到花蓮吉安的避難所。
題庫 evals/datasets/place_eval.jsonl 的每個地名標了它實際所在的鄉鎮（依地理，不依系統的地名資料），
評分看回答裡的避難所落在那幾個鄉鎮的比例：
  hit@1      第一間就在對的鄉鎮
  precision  前 5 間有幾成在對的鄉鎮
  llm        這一題還要交給模型（回答不是由程式直接給的）

量的是 build_context 的結果：程式直接回答時是回答本身，交給模型時是模型拿到的資料（依檢索順序）。
檢索把別的地方排在最前面，模型也答不對；不必等生成就量得出來。

    docker exec disaster_app python evals/run_place_eval.py --label place_baseline
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from evals.lib.gen_metrics import build_catalog, mentioned_shelters  # noqa: E402
from repositories.shelter_repository import ShelterRepository  # noqa: E402
from services.chat.service import ChatService  # noqa: E402
from services.retrieval.vector_store import VectorStore  # noqa: E402
from services.web.metrics import metrics  # noqa: E402

DEFAULT_CASES = os.path.join(ROOT, "evals", "datasets", "place_eval.jsonl")
RESULTS_DIR = os.path.join(ROOT, "evals", "results")
TEMPLATES = ["{place}附近有避難所嗎", "{place}最近的避難所在哪", "我在{place}，可以去哪裡避難"]
TOP = 5


def load_cases(path: str = DEFAULT_CASES) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def norm(township: str) -> str:
    return (township or "").replace("臺", "台")


def score(text: str, catalog, township_of: dict[str, str], expected: list[str]) -> dict:
    shown = mentioned_shelters(text or "", catalog)[:TOP]
    want = {norm(t) for t in expected}
    inside = [sid for sid in shown if norm(township_of.get(sid, "")) in want]
    return {
        "shown": shown,
        "hit1": bool(shown) and shown[0] in inside,
        "precision": round(len(inside) / len(shown), 3) if shown else 0.0,
    }


def summarize(rows: list[dict]) -> dict:
    def block(rs):
        if not rs:
            return {}
        return {
            "n": len(rs),
            "hit1": round(sum(r["hit1"] for r in rs) / len(rs), 3),
            "precision": round(sum(r["precision"] for r in rs) / len(rs), 3),
            "llm": round(sum(r["llm"] for r in rs) / len(rs), 3),
        }
    out = {"all": block(rows)}
    for kind in sorted({r["kind"] for r in rows}):
        out[kind] = block([r for r in rows if r["kind"] == kind])
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", default="place_eval")
    parser.add_argument("--cases", default=DEFAULT_CASES)
    args = parser.parse_args()

    repo = ShelterRepository()
    shelters = repo.get_all_shelters()
    catalog = build_catalog(shelters)
    township_of = {s.name: s.township for s in shelters}
    svc = ChatService(VectorStore(), repo)

    rows = []
    for i, case in enumerate(load_cases(args.cases)):
        question = TEMPLATES[i % len(TEMPLATES)].format(place=case["place"])
        context, early = svc.build_context(question)
        s = score(early if early is not None else context, catalog, township_of, case["townships"])
        rows.append({
            "place": case["place"], "kind": case["kind"], "question": question, "expected": case["townships"],
            "route": metrics.last_route(), "llm": early is None and context is not None,
            "shown_townships": [township_of.get(sid) for sid in s["shown"]],
            **s,
        })
        mark = "✓" if s["hit1"] else "✗"
        print(f"{mark} {question:<22} route={rows[-1]['route']:<14} 前{TOP}間鄉鎮={rows[-1]['shown_townships']}")

    summary = summarize(rows)
    print("\n=== 小地名評測 ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    os.makedirs(RESULTS_DIR, exist_ok=True)
    out = os.path.join(RESULTS_DIR, f"{args.label}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
