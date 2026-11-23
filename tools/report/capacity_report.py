"""
宜花東避難收容容量缺口報告。

每個鄉鎮（41 個）以人口重心為中心、半徑 10 公里，跑強震、淹水、淹水＋全鄉土石流紅色警戒三種情境，
算法跟 /api/simulate_disaster 一樣（services/simulation/scenario.py 決定目的地，最小成本流在資料庫裡解），
但不寫資料庫：避難所一律當成空的，結果只跟資料有關，同樣的資料一定得到同樣的報告。
整理與輸出在 services/simulation/capacity_report.py。

強震與淹水另外用開車跑一次基準疏散比例，記下最塞的路段（services/simulation/bottlenecks.py，跟地圖上選開車一樣）。

紅色警戒情境會暫時在 hazard_alerts 寫入來源為 capacity_report 的警戒（10 分鐘後自動失效），
跑完就刪掉；跑的這段時間網站上的路線也會繞開這些範圍。

    docker exec disaster_app python tools/capacity_report.py
    docker exec disaster_app python tools/capacity_report.py --township 宜蘭市 --township 秀林鄉   # 只跑幾個鄉鎮

輸出 reports/capacity_gap.json（完整數字）與 reports/capacity_gap.md（報告）。
"""
import argparse
import functools
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from repositories.shelter_repository import ShelterRepository  # noqa: E402
from services.chat.replies import SHELTER_DATASET_UPDATED  # noqa: E402
from services.geo.routing import (  # noqa: E402
    attach_evacuation_paths,
    evacuation_bottlenecks,
    evacuation_road_distances,
    solve_assignment,
)
from services.simulation.bottlenecks import END_EXCLUDED_M, PEOPLE_PER_VEHICLE  # noqa: E402
from services.simulation.capacity_report import (  # noqa: E402
    DEMAND_FACTORS,
    drive_summary,
    nobody_placed,
    render_markdown,
    shelter_usage,
    summarize_scenario,
    township_capacity,
)
from services.simulation.evacuation import Origin, origins_from, plan_evacuation  # noqa: E402
from services.simulation.population import EVAC_RATIO, PopulationModel  # noqa: E402
from services.simulation.scenario import destinations_for  # noqa: E402

OUT_DIR = os.path.join(ROOT, "reports")
RADIUS_KM = 10
MODE = "walk"
ALERT_SOURCE = "capacity_report"
ALERT_TTL_MINUTES = 10


def empty(shelters: list) -> list:
    """規劃看的是空的避難所：資料庫裡如果還留著上一次模擬的收容人數，報告不能跟著變"""
    return [replace(s, current_people=0) for s in shelters]


def with_people(origins: list[Origin], road_m: dict, people: dict[str, int]) -> tuple[list[Origin], dict]:
    """同一組起點換成另一種疏散人數；沒有人的起點拿掉，路網成本的序號跟著重編"""
    keep = [i for i, o in enumerate(origins) if people.get(o.name, 0) > 0]
    index = {old: new for new, old in enumerate(keep)}
    out = [Origin(origins[i].name, origins[i].lat, origins[i].lon, people[origins[i].name]) for i in keep]
    return out, {(index[i], j): c for (i, j), c in road_m.items() if i in index}


def drive_bottlenecks(repo, population: dict, lat: float, lon: float, dest: list, hazard: str | None) -> dict | None:
    """
    開車時的道路瓶頸：基準疏散比例、同一組目的地，分配與路線都改成開車，跟地圖上選開車模擬一樣。
    開車的分配不一定跟步行相同（時間成本不同），所以要重解一次，不能拿步行的分配來算
    """
    origins = origins_from(population, lat, lon)
    road_m = evacuation_road_distances(repo, origins, dest, hazard, "drive")
    plan = plan_evacuation(origins, dest, road_m, "drive", functools.partial(solve_assignment, repo))
    paths = attach_evacuation_paths(repo, origins, dest, plan["flows"], hazard, "drive")
    return drive_summary(evacuation_bottlenecks(repo, plan["flows"], paths, "drive"))


def run_scenario(repo, model, town: dict, kind: str, alerts: int = 0) -> dict | None:
    sim_type = "flood" if kind == "flood_red" else kind
    lat, lon = town["lat"], town["lon"]
    impacted = empty(repo.get_shelters_in_radius(lat, lon, RADIUS_KM))
    chosen = destinations_for(repo, impacted, sim_type)
    dest = chosen.shelters
    population = model.estimate(lat, lon, RADIUS_KM, sim_type, sum(s.capacity for s in dest))
    if population["fallback_estimate"]:
        return None
    if not dest:
        # 圈內沒有任何一間能去（例如沒有避難所列為震災適用）：所有需疏散的人都無法安置，這正是報告要看到的
        plans = {f: nobody_placed(round(population["estimated_evacuees"] * f)) for f in DEMAND_FACTORS}
        plans["all"] = nobody_placed(population["covered_population"])
        return summarize_scenario(town, kind, RADIUS_KM, population, dest, plans,
                                  excluded=len(chosen.excluded), not_designated=len(chosen.undesignated), alerts=alerts)
    # 起點用「所有受影響的人都疏散」產生，各種疏散人數共用同一組起點與路網成本
    everyone = origins_from({**population, "estimated_evacuees": population["covered_population"]}, lat, lon)
    road_m = evacuation_road_distances(repo, everyone, dest, chosen.hazard, MODE)
    solve = functools.partial(solve_assignment, repo)
    plans = {"all": plan_evacuation(everyone, dest, road_m, MODE, solve)}
    for factor in DEMAND_FACTORS:
        scaled = {**population, "estimated_evacuees": round(population["estimated_evacuees"] * factor)}
        people = {o.name: o.people for o in origins_from(scaled, lat, lon)}
        origins, costs = with_people(everyone, road_m, people)
        plans[factor] = plan_evacuation(origins, dest, costs, MODE, solve)
    # 紅色警戒情境看的是被切斷的村里，不另外跑開車
    drive = None if kind == "flood_red" else drive_bottlenecks(repo, population, lat, lon, dest, chosen.hazard)
    return summarize_scenario(town, kind, RADIUS_KM, population, dest, plans,
                              excluded=len(chosen.excluded), not_designated=len(chosen.undesignated), alerts=alerts,
                              drive=drive)


def debris_refs(repo) -> dict[str, list[str]]:
    """鄉鎮 → 這個鄉鎮的土石流潛勢溪流編號"""
    with repo._cursor() as cursor:
        cursor.execute(
            "SELECT town, ref_no FROM hazard_zones WHERE hazard_type = 'debris_flow' AND ref_no IS NOT NULL ORDER BY 1, 2"
        )
        out: dict[str, list[str]] = {}
        for town, ref in cursor.fetchall():
            out.setdefault(town, []).append(ref)
    return out


def red_alert(repo, refs: list[str]) -> None:
    repo.replace_alerts(ALERT_SOURCE, "debris_flow", [{"ref_no": r, "level": "red"} for r in refs], ALERT_TTL_MINUTES)


def meta(repo, model) -> dict:
    with repo._cursor() as cursor:
        cursor.execute("SELECT loaded_at FROM dataset_versions WHERE name = 'roads'")
        row = cursor.fetchone()
        cursor.execute("SELECT count(*) FROM hazard_zones WHERE hazard_type = 'debris_flow'")
        zones = cursor.fetchone()[0]
    with open(model.village_file, encoding="utf-8") as f:
        month = json.load(f)["population_source"]["statistic_yyymm"]
    return {
        "generated_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
        "shelter_dataset_updated": SHELTER_DATASET_UPDATED,
        # 內政部的統計年月是民國年：11509 → 民國 115 年 9 月
        "population_month": f"民國 {month[:3]} 年 {int(month[3:])} 月",
        "villages": len(model.villages),
        "roads_loaded": row[0].date().isoformat() if row else "未知",
        "debris_zones": zones,
        "radius_km": RADIUS_KM, "mode": MODE,
        "evacuation_ratios": {k: EVAC_RATIO[k] for k in ("earthquake", "flood")},
        "demand_factors": list(DEMAND_FACTORS),
        "drive": {"people_per_vehicle": PEOPLE_PER_VEHICLE, "end_excluded_m": END_EXCLUDED_M},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--township", action="append", help="只跑這些鄉鎮（可重複）；預設全部")
    parser.add_argument("--out", default=OUT_DIR)
    args = parser.parse_args()

    repo, model = ShelterRepository(), PopulationModel()
    shelters = empty(repo.get_all_shelters())
    flood = destinations_for(repo, shelters, "flood")
    towns = township_capacity(model.townships, shelters, {"flood": set(flood.excluded)})
    refs = debris_refs(repo)
    centers = [t for t in model.townships if not args.township or t["name"] in args.township]

    started = time.monotonic()
    scenarios = []
    red_alert(repo, [])  # 上一次沒跑完留下的
    try:
        for town in centers:
            for kind in ("earthquake", "flood"):
                if (sc := run_scenario(repo, model, town, kind)) is not None:
                    scenarios.append(sc)
            own = refs.get(town["name"].replace("臺", "台"), []) or refs.get(town["name"], [])
            if own:
                red_alert(repo, own)
                try:
                    if (sc := run_scenario(repo, model, town, "flood_red", alerts=len(own))) is not None:
                        scenarios.append(sc)
                finally:
                    red_alert(repo, [])
            print(f"{town['name']} 完成（{time.monotonic() - started:.0f} 秒）", flush=True)
    finally:
        red_alert(repo, [])

    report = {"meta": {**meta(repo, model), "shelters": len(shelters)}, "townships": towns,
              "scenarios": scenarios, "shelter_usage": shelter_usage(scenarios)}
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "capacity_gap.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, "capacity_gap.md"), "w", encoding="utf-8") as f:
        f.write(render_markdown(report))
    print(f"{len(scenarios)} 個情境，{time.monotonic() - started:.0f} 秒；輸出在 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
