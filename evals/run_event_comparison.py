"""
真實事件對照：拿一場真的發生過的災害，看系統的假設跟實際情況差多少。

這不是準確率評測：只有一場事件，數字來自新聞稿與報導，各自有時間點。它回答三個問題：
1. 實際開設、收容最多人的地方，在不在系統的避難所資料裡（系統推薦不出資料裡沒有的地方）
2. 系統假設的疏散比例（淹水 22% 的人進避難所），跟實際進收容所的比例差幾倍
3. 警戒區裡的避難所：系統的淹水模擬只避開土石流潛勢範圍，會不會把人分到警戒區裡面

事件資料在 evals/datasets/events/，每個數字都附出處。只讀資料檔（避難所、村里人口、容量缺口報告），
不連資料庫，同樣的資料一定得到同樣的結果。

    python evals/run_event_comparison.py
    python evals/run_event_comparison.py --event evals/datasets/events/mataian_2025.json
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services.simulation.evacuation import DESIGNATED_HAZARD  # noqa: E402
from services.simulation.population import EVAC_RATIO, VILLAGE_FILE  # noqa: E402

EVENT = os.path.join(ROOT, "evals", "datasets", "events", "mataian_2025.json")
SHELTER_DIR = os.path.join(ROOT, "data_for_refuge")
REPORT = os.path.join(ROOT, "reports", "capacity_gap.json")
RESULTS_DIR = os.path.join(ROOT, "evals", "results")


def _town(name: str) -> str:
    return name.replace("臺", "台")


def _bare(name: str) -> str:
    """容量缺口報告的名稱帶縣別前綴（「[HUALIEN] 光復國中」），資料檔沒有"""
    return name.split("] ", 1)[1] if name.startswith("[") and "] " in name else name


def load_shelters(folder: str = SHELTER_DIR) -> list[dict]:
    out = []
    for path in sorted(glob.glob(os.path.join(folder, "*_shelter.json"))):
        with open(path, encoding="utf-8") as f:
            out.extend(json.load(f))
    return out


def in_zone(zone: dict[str, list[str]], township: str, village: str) -> bool:
    return village in {v for t, vs in zone.items() if _town(t) == _town(township) for v in vs}


def zone_population(villages: list[dict], zone: dict[str, list[str]]) -> dict:
    """警戒區村里的戶籍人口；名單裡有、人口資料裡對不到的村里列出來，不默默少算"""
    rows = [v for v in villages if in_zone(zone, v["township"], v["village"])]
    found = {(_town(v["township"]), v["village"]) for v in rows}
    missing = sorted(f"{t}{v}" for t, vs in zone.items() for v in vs if (_town(t), v) not in found)
    return {"population": sum(int(v["pop"]) for v in rows), "villages": len(rows), "missing": missing}


def shelter_coverage(sheltered: dict, shelters: list[dict]) -> list[dict]:
    """
    實際收容處所對到資料集。對應寫在事件檔裡（dataset_name），不做模糊比對：
    「萬榮國小」跟資料裡的「萬榮社區活動中心」是兩個地方，自動比對會把它們當成同一間。
    這裡只檢查事件檔寫的對應站得住：寫了名稱的要真的存在，寫 null 的資料裡不能有同名的
    """
    names = {s["name"] for s in shelters}
    rows = []
    for s in sheltered["shelters"]:
        mapped = s.get("dataset_name")
        if mapped is not None and mapped not in names:
            raise SystemExit(f"事件檔把「{s['name']}」對到「{mapped}」，但資料集裡沒有這間")
        if mapped is None and s["name"] in names:
            raise SystemExit(f"資料集裡有「{s['name']}」，事件檔卻寫成不在資料裡")
        rows.append({"name": s["name"], "township": s["township"], "people": s["people"],
                     "dataset_name": mapped, "in_dataset": mapped is not None})
    return rows


def zone_shelters(shelters: list[dict], zone: dict[str, list[str]], hazard: str) -> list[dict]:
    """位在警戒區村里的避難所，以及它們是不是列為這類災害的避難收容處所"""
    label = DESIGNATED_HAZARD.get(hazard)
    rows = [
        {"name": s["name"], "township": s["township"], "village": s["village"], "capacity": int(s["total_vessel"]),
         "designated": not label or not s.get("hazards") or label in s["hazards"]}
        for s in shelters if in_zone(zone, s["township"], s.get("village") or "")
    ]
    return sorted(rows, key=lambda r: (r["township"], r["village"], r["name"]))


def report_scenarios(report: dict | None, zone: dict[str, list[str]], hazard: str) -> list[dict]:
    """容量缺口報告裡以警戒區鄉鎮為中心的同類災害情境：系統當時會把人分到哪些避難所"""
    if not report:
        return []
    towns = {_town(t) for t in zone}
    out = []
    for sc in report["scenarios"]:
        if sc["kind"] != hazard or _town(sc["township"]) not in towns:
            continue
        out.append({"township": sc["township"], "radius_km": sc["radius_km"],
                    "covered_population": sc["covered_population"], "evacuees": sc["evacuees"]["1.0"],
                    "used": sorted(_bare(n) for n in sc["used"])})
    return out


def compare(event: dict, shelters: list[dict], villages: list[dict], ratio: float, report: dict | None = None) -> dict:
    zone = event["warning_zone"]["villages"]
    pop = zone_population(villages, zone)
    if pop["missing"]:
        raise SystemExit(f"警戒區村里在人口資料裡對不到：{'、'.join(pop['missing'])}")
    people = pop["population"]
    evac = event["evacuation"]
    left_home = evac["evacuated"] - evac["vertical"]
    sheltered = sorted(event["sheltered"], key=lambda s: s["total"])
    low, high = sheltered[0], sheltered[-1]

    by_shelter = next((s for s in event["sheltered"] if s["shelters"]), None)
    coverage = shelter_coverage(by_shelter, shelters) if by_shelter else []
    listed = sum(r["people"] for r in coverage)
    missing_people = sum(r["people"] for r in coverage if not r["in_dataset"])

    inside = zone_shelters(shelters, zone, event["hazard"])
    inside_names = {r["name"] for r in inside if r["designated"]}
    scenarios = report_scenarios(report, zone, event["hazard"])
    for sc in scenarios:
        sc["used_in_zone"] = sorted(n for n in sc["used"] if n in inside_names)

    system = round(people * ratio)
    return {
        "event": {k: event[k] for k in ("id", "name", "date", "hazard")},
        "zone": {"villages": pop["villages"], "population": people},
        "demand": {
            "system_ratio": ratio,
            "system_evacuees": system,
            "evacuated": evac["evacuated"], "vertical": evac["vertical"], "left_home": left_home,
            "sheltered_low": low["total"], "sheltered_high": high["total"],
            "sheltered_high_at_least": bool(high.get("at_least")),
            "share": {
                "evacuated": round(evac["evacuated"] / people, 4),
                "left_home": round(left_home / people, 4),
                "sheltered_low": round(low["total"] / people, 4),
                "sheltered_high": round(high["total"] / people, 4),
            },
            # 系統估的進避難所人數是實際收容的幾倍（實際取範圍的兩端）
            "overestimate": [round(system / high["total"], 1), round(system / low["total"], 1)],
        },
        "coverage": {
            "as_of": by_shelter["as_of"] if by_shelter else None,
            "shelters": coverage, "people": listed, "people_not_in_dataset": missing_people,
            "share_not_in_dataset": round(missing_people / listed, 4) if listed else None,
        },
        "zone_shelters": {
            "shelters": inside,
            "designated": len(inside_names),
            "designated_capacity": sum(r["capacity"] for r in inside if r["designated"]),
            "scenarios": scenarios,
        },
    }


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def render(summary: dict) -> str:
    d, c, z = summary["demand"], summary["coverage"], summary["zone_shelters"]
    high = f"{d['sheltered_high']:,}{'+' if d['sheltered_high_at_least'] else ''}"
    lines = [
        f"# {summary['event']['name']}（{summary['event']['date']}）",
        "",
        f"警戒區 {summary['zone']['villages']} 個村里，戶籍人口 {summary['zone']['population']:,} 人",
        "",
        "## 進避難所的人數",
        "",
        "| | 人數 | 占警戒區人口 |",
        "|---|---:|---:|",
        f"| 系統假設（淹水 {_pct(d['system_ratio'])}） | {d['system_evacuees']:,} | {_pct(d['system_ratio'])} |",
        f"| 實際疏散 | {d['evacuated']:,} | {_pct(d['share']['evacuated'])} |",
        f"| 其中離開家（不是垂直避難） | {d['left_home']:,} | {_pct(d['share']['left_home'])} |",
        f"| 實際進收容所 | {d['sheltered_low']:,}～{high} | "
        f"{_pct(d['share']['sheltered_low'])}～{_pct(d['share']['sheltered_high'])} |",
        "",
        f"系統估的是實際收容人數的 {d['overestimate'][0]}～{d['overestimate'][1]} 倍",
        "",
        f"## 實際收容處所在不在資料裡（{c['as_of']}）",
        "",
        "| 收容處所 | 人數 | 資料集 |",
        "|---|---:|---|",
    ]
    for r in c["shelters"]:
        lines.append(f"| {r['name']} | {r['people']:,} | {('✓ ' + r['dataset_name']) if r['in_dataset'] else '✗ 沒有'} |")
    if c["people"]:
        lines += ["", f"{c['people']:,} 人裡有 {c['people_not_in_dataset']:,} 人（{_pct(c['share_not_in_dataset'])}）"
                      "住在資料集裡沒有的地方"]
    lines += [
        "",
        "## 警戒區裡的避難所",
        "",
        f"資料集裡位在警戒區村里、列為這類災害避難收容處所的有 {z['designated']} 間，容量 {z['designated_capacity']:,} 人",
    ]
    for sc in z["scenarios"]:
        lines.append(f"- 容量缺口報告「{sc['township']}」{sc['radius_km']} 公里的情境：分到人的 {len(sc['used'])} 間裡，"
                     f"{len(sc['used_in_zone'])} 間在警戒區內")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--event", default=EVENT)
    parser.add_argument("--out", help="結果 JSON；預設 evals/results/event_<事件 id>.json")
    args = parser.parse_args()

    with open(args.event, encoding="utf-8") as f:
        event = json.load(f)
    with open(VILLAGE_FILE, encoding="utf-8") as f:
        villages = json.load(f)["villages"]
    report = None
    if os.path.exists(REPORT):
        with open(REPORT, encoding="utf-8") as f:
            report = json.load(f)

    summary = compare(event, load_shelters(), villages, EVAC_RATIO[event["hazard"]], report)
    out = args.out or os.path.join(RESULTS_DIR, f"event_{event['id']}.json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(render(summary))
    print(f"結果寫到 {os.path.relpath(out, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
