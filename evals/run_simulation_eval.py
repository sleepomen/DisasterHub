"""
模擬追問評測：模擬跑完之後問「每間分配幾人」「還有多少人沒安置」「哪些受影響」「哪些被排除」，
模型回答的名稱與數字是否跟後端的分配一致。

最近避難所原本也交給模型重抄，2026-10-31 實測把第 2 名的名稱抄成第 1 名的（見 docs/decisions.md）。
模擬追問是剩下唯二還會打模型的路徑之一，資料一樣是後端算好的結構化數字，這裡量它會不會犯同樣的錯。

走真實的 API：登入、/api/simulate_disaster、/api/chat、/api/reset_simulation，跟使用者一樣。
所以會暫時改動資料庫的收容人數，每個情境開始前與全部結束後都會重置。
南澳情境另外插入測試用的紅色警戒（source="eval"），結束後清掉。

評分對象：
- 預設評使用者實際看到的完整回答。2026-11-02 起模擬追問全部由程式回答（services/simulation/text.py），
  「有沒有被排除」的答案本身就是排除的說明，不能拿掉。
- --model-text-only 只評模型寫的部分：把程式附在最後的說明（被排除、靠近潛勢範圍、路線）用同一個函式算出來
  先拿掉。第一次的結果 evals/results/sim_llama3.2-3b.json（還交給模型回答的時候）是這樣評的。

    docker exec disaster_app python evals/run_simulation_eval.py
    docker exec disaster_app python evals/run_simulation_eval.py --label sim_llama3.2-3b
"""
import argparse
import json
import os
import re
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from evals.lib.gen_metrics import build_catalog, integers_in, mentioned_shelters, suspected_invented_names  # noqa: E402
from repositories.shelter_repository import ShelterRepository  # noqa: E402
from services.retrieval.shelter_profile import strip_region_tag  # noqa: E402
from services.simulation.text import hazard_lines  # noqa: E402

BASE_URL = os.environ.get("EVAL_BASE_URL", "http://localhost:8501")
RESULTS_DIR = os.path.join(ROOT, "evals", "results")
ALERT_SOURCE = "eval"
NOTE_PREFIX = ("以下", "These ", "This shelter", "No safe road")
STILL_USED = "仍列為疏散目的地"
LIST_NUMBER = re.compile(r"^\s*\d+\s*[.、)）]\s*")

SCENARIOS = [
    {"id": "hualien_quake", "label": "花蓮市 強震 10 km 步行", "lat": 23.98, "lon": 121.60, "radius": 10,
     "type": "earthquake", "mode": "walk"},
    {"id": "taitung_flood", "label": "台東市 淹水 10 km 開車", "lat": 22.76, "lon": 121.14, "radius": 10,
     "type": "flood", "mode": "drive"},
    {"id": "nanao_red_alert", "label": "南澳 淹水 10 km 步行＋紅色警戒（測試資料）", "lat": 24.46, "lon": 121.80,
     "radius": 10, "type": "flood", "mode": "walk",
     "alerts": ["宜縣DF096", "宜縣DF097", "宜縣DF115", "宜縣DF116"]},
    {"id": "yilan_small", "label": "宜蘭市 強震 5 km 步行", "lat": 24.757, "lon": 121.753, "radius": 5,
     "type": "earthquake", "mode": "walk"},
    {"id": "yuli_wide", "label": "玉里 淹水 15 km 開車", "lat": 23.335, "lon": 121.31, "radius": 15,
     "type": "flood", "mode": "drive"},
]

QUESTIONS = {
    "allocation": "這次模擬每間避難所分配了多少人？",
    "unplaced": "這次模擬還有多少人沒有被安置？",
    "impacted": "哪些避難所受影響？",
    "excluded": "這次模擬有沒有避難所被排除？為什麼？",
}


def login(session: requests.Session) -> None:
    res = session.post(f"{BASE_URL}/api/login", json={
        "username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"],
    }, timeout=30)
    res.raise_for_status()


def reset(session: requests.Session) -> None:
    session.post(f"{BASE_URL}/api/reset_simulation", timeout=600).raise_for_status()


def ask(session: requests.Session, question: str) -> tuple[str, float]:
    """問一題，把 SSE 的片段接起來；回傳 (回答, 秒數)"""
    started = time.monotonic()
    for _ in range(10):
        res = session.post(f"{BASE_URL}/api/chat", json={"message": question}, stream=True, timeout=600)
        if res.status_code != 429:
            break
        # 程式回答只要 0.1 秒，會撞到聊天的每分鐘題數上限（CHAT_RATE_LIMIT）：照 Retry-After 等一下再問
        res.close()
        time.sleep(int(res.headers.get("Retry-After") or 10))
        started = time.monotonic()
    with res:
        res.raise_for_status()
        parts = []
        for line in res.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                event = json.loads(line[5:])
                parts.append(event.get("text") or "")
    return "".join(parts).strip(), round(time.monotonic() - started, 1)


def model_text(reply: str, plan: dict) -> str:
    """拿掉由程式附在最後的說明，只留模型寫的部分"""
    notes = set(hazard_lines(plan))
    return "\n".join(line for line in reply.splitlines() if line.strip() not in notes).strip()


def line_shelter(line: str, catalog, scope: set[str]) -> str | None:
    """
    一行講的是哪一間。同名的避難所（宜蘭、台東都有「建業社區活動中心」）優先算成這次模擬範圍內的那間；
    名稱互為子字串時（中華國小／中華國小活動中心）取最長的那個
    """
    ids = mentioned_shelters(line, catalog)
    if not ids:
        return None
    in_scope = [i for i in ids if i in scope]
    return max(in_scope or ids, key=lambda i: len(strip_region_tag(i)))


def line_numbers(line: str) -> set[int]:
    """一行裡的數字，不含開頭的清單編號（「26. 黎明國小」的 26 不是人數）"""
    return integers_in(LIST_NUMBER.sub("", line))


def pair_lines(text: str, catalog, expected: dict[str, set[int]]) -> dict:
    """
    逐行檢查「名稱 + 數字」：一行提到某間避難所、而且有數字時，數字裡要有那一間自己的值（expected[id]）。
    有數字但沒有自己的值，就是名稱和數字配錯了（2026-10-31 最近避難所就是這樣錯的）
    """
    correct, wrong, unknown = [], [], []
    for line in text.splitlines():
        # 「以下避難所靠近…：甲（約 137 公尺）、乙…」這類說明句：數字是距離或間數，不是某一間的人數
        if line.strip().startswith(NOTE_PREFIX) or len(set(mentioned_shelters(line, catalog))) > 1:
            continue
        sid = line_shelter(line, catalog, set(expected))
        if sid is None:
            continue
        numbers = line_numbers(line)
        if not numbers:
            continue
        if sid not in expected:
            unknown.append(line.strip())
        elif numbers & expected[sid]:
            correct.append(sid)
        else:
            wrong.append(line.strip())
    return {"correct": correct, "wrong": wrong, "not_in_plan": unknown}


def score(kind: str, text: str, catalog, sim: dict) -> dict:
    plan = sim["evacuation"]
    impacted = {s["name"]: s for s in sim["impacted_shelters"]}
    in_scope = {strip_region_tag(n) for n in impacted}
    mentioned = set(mentioned_shelters(text, catalog))
    result = {
        "invented": suspected_invented_names(text, catalog),
        # 提到範圍外的避難所：資料裡沒有它，模型從別處拼湊或記錯。跟範圍內同名的不算
        "outside_simulation": sorted(m for m in mentioned - set(impacted) if strip_region_tag(m) not in in_scope),
    }
    if kind == "allocation":
        assigned = plan.get("assigned") or {}
        pairs = pair_lines(text, catalog, {sid: {n} for sid, n in assigned.items()})
        # 避難所被填滿時，分配人數剛好等於容量或模擬前的剩餘空位，抄容量也會被算成對。
        # 只在分配人數跟這兩個都不同的避難所上量，才分得出「答了分配」還是「抄了容量」
        distinct = {
            sid for sid, n in assigned.items()
            if sid in impacted and n not in (int(impacted[sid]["capacity"]), int(impacted[sid]["remaining"]))
        }
        result.update({
            "expected": len(assigned),
            "covered": len(mentioned & set(assigned)),
            "pairs_correct": len(pairs["correct"]),
            "pairs_wrong": pairs["wrong"],
            "listed_but_not_assigned": pairs["not_in_plan"],
            "distinct_expected": len(distinct),
            "distinct_correct": len(set(pairs["correct"]) & distinct),
        })
    elif kind == "impacted":
        # 回應裡的收容人數是分配前的；模型看到的快照已經寫回分配，兩種都算對
        assigned = plan.get("assigned") or {}
        values = {
            sid: {
                int(s["capacity"]), int(s["current_ppl"]), int(s["remaining"]),
                int(s["current_ppl"]) + assigned.get(sid, 0), int(s["remaining"]) - assigned.get(sid, 0),
            }
            for sid, s in impacted.items()
        }
        pairs = pair_lines(text, catalog, values)
        result.update({
            "expected": len(impacted),
            "covered": len(mentioned & set(impacted)),
            "pairs_correct": len(pairs["correct"]),
            "pairs_wrong": pairs["wrong"],
        })
    elif kind == "unplaced":
        unplaced = int(plan.get("unplaced") or 0)
        numbers = integers_in(text.replace(",", ""))
        result.update({
            "expected_unplaced": unplaced,
            # 「約 1.2 萬人」這種寫法也算：prompt 裡的人口摘要就是這樣寫的
            "states_unplaced": unplaced in numbers or (unplaced == 0 and ("足以" in text or "全部" in text)),
        })
    elif kind == "excluded":
        expected = set(plan.get("excluded") or {}) | set(plan.get("undesignated") or []) | set(plan.get("unreachable") or [])
        # 明寫「仍列為疏散目的地」的句子（靠近潛勢範圍的提醒）不算成說它被排除
        claimed = set(mentioned_shelters(
            "\n".join(line for line in text.splitlines() if STILL_USED not in line), catalog,
        ))
        result.update({
            "expected": len(expected),
            "covered": len(mentioned & expected),
            # 說成被排除、其實有分配到人的
            "assigned_but_mentioned": sorted(claimed & set(plan.get("assigned") or {}) - expected),
        })
    return result


def set_alerts(refs: list[str]) -> None:
    ShelterRepository().replace_alerts(
        ALERT_SOURCE, "debris_flow", [{"ref_no": r, "level": "red"} for r in refs], ttl_minutes=60,
    )


def summarize(rows: list[dict]) -> dict:
    def total(kind, key):
        return sum(r["score"].get(key, 0) for r in rows if r["kind"] == kind)

    def count(kind, key):
        return sum(len(r["score"].get(key, [])) for r in rows if r["kind"] == kind)

    summary = {}
    for kind in ("allocation", "impacted"):
        expected, covered = total(kind, "expected"), total(kind, "covered")
        right, wrong = total(kind, "pairs_correct"), count(kind, "pairs_wrong")
        summary[kind] = {
            "coverage": round(covered / expected, 3) if expected else None,
            "pair_accuracy": round(right / (right + wrong), 3) if right + wrong else None,
            "pairs_wrong": wrong,
        }
    summary["allocation"]["listed_but_not_assigned"] = count("allocation", "listed_but_not_assigned")
    distinct = total("allocation", "distinct_expected")
    summary["allocation"]["distinct_recall"] = round(total("allocation", "distinct_correct") / distinct, 3) if distinct else None
    summary["allocation"]["distinct_cases"] = distinct
    summary["answers_cut_off"] = sum(
        1 for r in rows
        if r["kind"] in ("allocation", "impacted") and r["score"]["covered"] < r["score"]["expected"]
    )
    un = [r for r in rows if r["kind"] == "unplaced"]
    summary["unplaced"] = {"correct": sum(r["score"]["states_unplaced"] for r in un), "of": len(un)}
    ex = [r for r in rows if r["kind"] == "excluded"]
    summary["excluded"] = {
        "coverage": round(total("excluded", "covered") / total("excluded", "expected"), 3) if total("excluded", "expected") else None,
        "assigned_but_mentioned": count("excluded", "assigned_but_mentioned"),
        "of": len(ex),
    }
    summary["invented_names"] = sum(len(r["score"]["invented"]) for r in rows)
    summary["outside_simulation"] = sum(len(r["score"]["outside_simulation"]) for r in rows)
    seconds = sorted(r["seconds"] for r in rows)
    summary["seconds_p50"] = seconds[len(seconds) // 2] if seconds else None
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", default="simulation_eval")
    parser.add_argument("--scenarios", nargs="*", help="只跑這幾個情境 id")
    parser.add_argument("--model-text-only", action="store_true",
                        help="拿掉程式附在最後的說明，只評模型寫的部分（回答還交給模型時用）")
    args = parser.parse_args()

    catalog = build_catalog(ShelterRepository().get_all_shelters())
    session = requests.Session()
    login(session)
    rows = []
    scenarios = [s for s in SCENARIOS if not args.scenarios or s["id"] in args.scenarios]
    try:
        for sc in scenarios:
            reset(session)
            set_alerts(sc.get("alerts") or [])
            res = session.post(f"{BASE_URL}/api/simulate_disaster", json={
                k: sc[k] for k in ("lat", "lon", "radius", "type", "mode")
            }, timeout=600)
            res.raise_for_status()
            sim = res.json()
            plan = sim["evacuation"]
            print(f"\n== {sc['label']}：受影響 {sim['impacted_count']} 間，分配 {len(plan['assigned'])} 間，"
                  f"安置 {plan['placed']} 人、無法安置 {plan['unplaced']} 人")
            for kind, question in QUESTIONS.items():
                reply, seconds = ask(session, question)
                text = model_text(reply, plan) if args.model_text_only else reply
                s = score(kind, text, catalog, sim)
                rows.append({"scenario": sc["id"], "kind": kind, "question": question, "seconds": seconds,
                             "reply": reply, "scored_text": text, "score": s})
                brief = {k: (len(v) if isinstance(v, list) else v) for k, v in s.items()}
                print(f"  [{kind}] {seconds}s {brief}")
    finally:
        set_alerts([])
        reset(session)

    summary = summarize(rows)
    print("\n=== 模擬追問評測 ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    os.makedirs(RESULTS_DIR, exist_ok=True)
    out = os.path.join(RESULTS_DIR, f"{args.label}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "model_text_only": args.model_text_only, "scenarios": scenarios, "rows": rows},
                  f, ensure_ascii=False, indent=2)
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
