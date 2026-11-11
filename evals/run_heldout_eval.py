"""
外部題目評測：沒看過系統、也沒看過現有題庫的人出的題，系統答得怎樣。

其他評測的題目都是開發時自己寫的，規則也是照著這些題目調的，分數難免偏高。
這裡的題目由別人出（出題說明在 evals/datasets/heldout/README.md），跑法跟網站一樣走完整的 ChatService
（真的檢索、資料庫、防災知識與 Ollama），再由人逐題判定。分兩步：

    # 1. 讓系統回答，寫出一份待評的表（Excel 可開）
    docker exec disaster_app python evals/run_heldout_eval.py answer evals/datasets/heldout/questions.csv
    # 2. 在表的「判定」欄填 正確／部分正確／錯誤／正確拒答／不該拒答，存檔後計分
    docker exec disaster_app python evals/run_heldout_eval.py score evals/results/heldout_answers.csv

計分以「正確 + 正確拒答」為答對，附 bootstrap 信賴區間，並依路由、是否經過模型分開算。
題目是在沒有模擬進行時問的，模擬追問不在這份評測裡（evals/run_simulation_eval.py 量的是那部分）。
"""
import argparse
import collections
import csv
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from evals.lib.metrics import bootstrap_ci  # noqa: E402

RESULTS_DIR = os.path.join(ROOT, "evals", "results")
ANSWERS = os.path.join(RESULTS_DIR, "heldout_answers.csv")
SCORES = os.path.join(RESULTS_DIR, "heldout_eval.json")

QUESTION_FIELDS = ("id", "出題者", "問題", "期待的回答")
ANSWER_FIELDS = (*QUESTION_FIELDS, "路由", "不經過模型", "秒數", "系統回答", "判定", "備註")
# 判定 → 算不算答對
VERDICTS = {"正確": True, "正確拒答": True, "部分正確": False, "錯誤": False, "不該拒答": False}


def read_csv(path: str) -> list[dict]:
    # utf-8-sig：Excel 存的 CSV 開頭有 BOM
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{k.strip(): (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(f)]


def write_csv(path: str, rows: list[dict], fields) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 帶 BOM，Excel 直接開才不會變亂碼
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_questions(path: str) -> list[dict]:
    rows = read_csv(path)
    missing = [k for k in ("問題",) if rows and k not in rows[0]]
    if missing:
        raise SystemExit(f"{path} 缺少欄位：{', '.join(missing)}（範本見 evals/datasets/heldout/questions_template.csv）")
    questions = []
    for i, row in enumerate(rows, 1):
        if not row.get("問題"):
            continue
        questions.append({**row, "id": row.get("id") or str(i)})
    seen = collections.Counter(q["問題"] for q in questions)
    dupes = [q for q, n in seen.items() if n > 1]
    if dupes:
        print(f"注意：{len(dupes)} 題重複出現，計分時各算一次：{'、'.join(dupes[:5])}", flush=True)
    return questions


def answer(questions: list[dict]) -> list[dict]:
    from api.deps import chat_service
    from services.web.metrics import metrics

    chat_service.clear_simulation()
    rows = []
    def generated() -> int:
        return metrics.snapshot()["counters"].get("chat.answer.generated", 0)

    for i, q in enumerate(questions, 1):
        metrics.clear_route()
        before = generated()
        started = time.perf_counter()
        reply = chat_service.chat(q["問題"])
        seconds = time.perf_counter() - started
        route = metrics.last_route()
        # 同一條路由（例如 rag）可能由程式直接排好，也可能交給模型；看網站自己的計數器才分得出來
        by_code = generated() == before
        rows.append({**q, "路由": route, "不經過模型": "是" if by_code else "否",
                     "秒數": f"{seconds:.1f}", "系統回答": reply, "判定": "", "備註": ""})
        print(f"[{i:>3}/{len(questions)}] {route:<20} {seconds:5.1f}s  {q['問題']}", flush=True)
    return rows


def group_stats(rows: list[dict]) -> dict:
    hits = [1.0 if VERDICTS[r["判定"]] else 0.0 for r in rows]
    low, high = bootstrap_ci(hits)
    counts = collections.Counter(r["判定"] for r in rows)
    return {"count": len(rows), "correct": int(sum(hits)), "accuracy": round(sum(hits) / len(rows), 4),
            "ci95": [low, high], "verdicts": dict(counts)}


def score(rows: list[dict]) -> dict:
    unknown = sorted({r.get("判定", "") for r in rows} - set(VERDICTS))
    if unknown:
        raise SystemExit(f"「判定」欄有無法辨識的值：{unknown}；只能填 {'／'.join(VERDICTS)}")
    by_route = collections.defaultdict(list)
    by_path = collections.defaultdict(list)
    by_author = collections.defaultdict(list)
    for r in rows:
        by_route[r.get("路由") or "-"].append(r)
        by_path["程式組的回答" if r.get("不經過模型") == "是" else "模型生成"].append(r)
        by_author[r.get("出題者") or "-"].append(r)
    return {
        "overall": group_stats(rows),
        "by_route": {k: group_stats(v) for k, v in sorted(by_route.items())},
        "by_answer_path": {k: group_stats(v) for k, v in sorted(by_path.items())},
        "authors": len(by_author),
        "questions_per_author": {k: len(v) for k, v in sorted(by_author.items())},
    }


def print_score(summary: dict) -> None:
    def line(name, s):
        lo, hi = s["ci95"]
        print(f"{name:<20} {s['count']:>4} {s['correct']:>5} {s['accuracy']:>7.3f}  [{lo:.3f}, {hi:.3f}]")
    print(f"\n=== 外部題目評測：{summary['authors']} 位出題者 ===\n")
    print(f"{'':<20} {'題數':>4} {'答對':>5} {'正確率':>7}  95% 信賴區間")
    line("全部", summary["overall"])
    print()
    for k, s in summary["by_answer_path"].items():
        line(k, s)
    print()
    for k, s in summary["by_route"].items():
        line(k, s)
    print("\n判定分布：", summary["overall"]["verdicts"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="step", required=True)
    a = sub.add_parser("answer", help="讓系統回答，寫出待評的表")
    a.add_argument("questions")
    a.add_argument("--out", default=ANSWERS)
    s = sub.add_parser("score", help="讀填好判定的表，算正確率")
    s.add_argument("answers", nargs="?", default=ANSWERS)
    s.add_argument("--out", default=SCORES)
    args = parser.parse_args()

    if args.step == "answer":
        if os.path.exists(args.out) and any(r.get("判定") for r in read_csv(args.out)):
            raise SystemExit(f"{args.out} 已經有填好的判定，換一個 --out 以免覆蓋")
        rows = answer(load_questions(args.questions))
        write_csv(args.out, rows, ANSWER_FIELDS)
        print(f"\n已寫出 {args.out}：在「判定」欄填 {'／'.join(VERDICTS)}，再執行 score")
        return 0

    rows = read_csv(args.answers)
    blank = [r.get("id", "?") for r in rows if not r.get("判定")]
    if blank:
        raise SystemExit(f"還有 {len(blank)} 題沒填判定（id：{', '.join(blank[:10])}）")
    summary = score(rows)
    print_score(summary)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=1)
    print(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
