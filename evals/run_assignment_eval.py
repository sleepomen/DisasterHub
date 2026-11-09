"""
疏散分配評測。情境是每個鄉鎮的人口重心當模擬中心、半徑 5 與 10 公里、強震與淹水（依適用災害排除避難所），
起點、避難所與每一組的通行成本都跟 /api/simulate_disaster 一樣算（services/simulation/evacuation.pair_costs）。

量三件事：
1. 資料庫解（pgr_maxFlowMinCost，模擬 API 用的）跟純 Python 的 min_cost_flow 是不是同一個答案（安置人數、總時間）。
2. 最小成本流跟「全域最近優先」貪婪法（2026-11-02 以前用的）差多少：安置人數、總時間、每人平均分鐘、最遠一趟。
3. 起點從鄉鎮重心換成村里差多少：同樣的需疏散人數，以前全部從鄉鎮重心出發，現在從各村里出發。
   另外記下兩種起點離最近道路多遠（那一段不論步行或開車都算步行時間）。

    docker exec disaster_app python evals/run_assignment_eval.py
"""
import argparse
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from repositories.shelter_repository import ShelterRepository  # noqa: E402
from services.geo.routing import evacuation_road_distances  # noqa: E402
from services.simulation.evacuation import Origin, apportion, origins_from, pair_costs, undesignated  # noqa: E402
from services.simulation.min_cost_flow import min_cost_flow  # noqa: E402
from services.simulation.population import PopulationModel  # noqa: E402

RESULTS_DIR = os.path.join(ROOT, "evals", "results")
RADII = (5, 10)
KINDS = ("earthquake", "flood")


def greedy(supply: list[int], capacity: list[int], cost: dict[tuple[int, int], float]) -> dict[tuple[int, int], int]:
    """以前的分配：依每人成本由小到大，能塞多少塞多少"""
    need, room, moved = list(supply), list(capacity), {}
    for (i, j), _ in sorted(cost.items(), key=lambda kv: (kv[1], kv[0])):
        people = min(need[i], room[j])
        if people > 0:
            need[i] -= people
            room[j] -= people
            moved[(i, j)] = people
    return moved


def measure(moved: dict, cost: dict) -> dict:
    placed = sum(moved.values())
    total = sum(people * cost[pair] for pair, people in moved.items())
    return {
        "placed": placed,
        "person_hours": round(total / 3600, 1),
        "minutes_per_person": round(total / placed / 60, 2) if placed else 0.0,
        "longest_trip_minutes": round(max((cost[p] for p in moved), default=0) / 60, 1),
    }


def township_origins(population: dict, model: PopulationModel) -> list[Origin]:
    """以前的起點：同樣的需疏散人數，依受影響人口分給各鄉鎮，全部從鄉鎮重心出發"""
    centers = {(t["county"], t["name"]): t for t in model.townships}
    towns = population["townships"]
    shares = apportion(int(population["estimated_evacuees"]), {t["name"]: int(t["weight"]) for t in towns})
    return [
        Origin(t["name"], centers[(t["county"], t["name"])]["lat"], centers[(t["county"], t["name"])]["lon"], shares[t["name"]])
        for t in towns if shares[t["name"]] > 0
    ]


def road_gap(repo, points: list[tuple[float, float]]) -> dict:
    """每個點離最近道路的距離（公尺）：中位數、最大、超過 200 公尺的個數"""
    with repo._cursor() as cursor:
        cursor.execute(ShelterRepository.SNAP_SQL,
                       (list(range(len(points))), [lon for _, lon in points], [lat for lat, _ in points]))
        meters = sorted(row[3] for row in cursor.fetchall())
    return {"count": len(meters), "median_m": round(statistics.median(meters)), "max_m": round(meters[-1]),
            "over_200_m": sum(m > 200 for m in meters)}


def problem(repo, origins, shelters, mode):
    road = evacuation_road_distances(repo, origins, shelters, None, mode)
    cost = {pair: c for pair, (c, _, _) in pair_costs(origins, shelters, road, mode).items()}
    return [o.people for o in origins], [max(0, int(s.remaining)) for s in shelters], cost


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("walk", "drive"), default="walk")
    parser.add_argument("--label", default="assignment_eval")
    args = parser.parse_args()

    repo, model = ShelterRepository(), PopulationModel()
    rows = []
    for town in model.townships:
        for radius in RADII:
            for kind in KINDS:
                impacted = repo.get_shelters_in_radius(town["lat"], town["lon"], radius)
                skip = set(undesignated(impacted, kind))
                shelters = [s for s in impacted if s.name not in skip]
                if not shelters:
                    continue
                population = model.estimate(town["lat"], town["lon"], radius, kind, sum(s.remaining for s in shelters))
                villages = origins_from(population, town["lat"], town["lon"])
                if not villages or not population["townships"]:
                    continue
                started = time.monotonic()
                supply, capacity, cost = problem(repo, villages, shelters, args.mode)
                matrix_s = time.monotonic() - started
                started = time.monotonic()
                db = repo.assignment_flows(supply, capacity, cost)
                db_s = time.monotonic() - started
                started = time.monotonic()
                python = min_cost_flow(supply, capacity, cost)
                python_s = time.monotonic() - started
                t_supply, t_capacity, t_cost = problem(repo, township_origins(population, model), shelters, args.mode)
                rows.append({
                    "scenario": f"{town['name']} {radius} km {kind}", "villages": len(villages),
                    "townships": len(t_supply), "shelters": len(shelters), "pairs": len(cost),
                    "demand_fits": sum(supply) <= sum(capacity),
                    "seconds": {"cost_matrix": round(matrix_s, 3), "db_solver": round(db_s, 3), "python_solver": round(python_s, 3)},
                    "min_cost": measure(db, cost), "python": measure(python, cost),
                    "greedy": measure(greedy(supply, capacity, cost), cost),
                    "township_origins": measure(min_cost_flow(t_supply, t_capacity, t_cost), t_cost),
                })
        print(f"{town['name']} 完成，累計 {len(rows)} 個情境", flush=True)

    def gap(r, base="greedy"):
        g, m = r[base]["person_hours"], r["min_cost"]["person_hours"]
        return (g - m) / g if g else 0.0

    def block(rs):
        if not rs:
            return {}
        gaps = [gap(r) for r in rs]
        return {
            "scenarios": len(rs),
            "placed_differs": sum(r["greedy"]["placed"] != r["min_cost"]["placed"] for r in rs),
            "min_cost_better": sum(g > 1e-6 for g in gaps),
            "greedy_extra_time_mean": round(statistics.mean(gaps), 4),
            "greedy_extra_time_max": round(max(gaps), 4),
            "minutes_per_person": [round(statistics.mean(r[k]["minutes_per_person"] for r in rs), 1) for k in ("greedy", "min_cost")],
            "longest_trip_minutes": [round(statistics.mean(r[k]["longest_trip_minutes"] for r in rs), 1) for k in ("greedy", "min_cost")],
        }

    def origins_block(rs):
        diffs = [r["min_cost"]["minutes_per_person"] - r["township_origins"]["minutes_per_person"] for r in rs]
        return {
            "minutes_per_person": [round(statistics.mean(r[k]["minutes_per_person"] for r in rs), 1)
                                   for k in ("township_origins", "min_cost")],
            "longest_trip_minutes": [round(statistics.mean(r[k]["longest_trip_minutes"] for r in rs), 1)
                                     for k in ("township_origins", "min_cost")],
            "per_person_difference_minutes": {
                "mean": round(statistics.mean(diffs), 1), "min": round(min(diffs), 1), "max": round(max(diffs), 1),
            },
            "scenarios_longer_with_villages": sum(d > 0 for d in diffs),
        }

    agree = [r for r in rows if r["python"]["placed"] == r["min_cost"]["placed"]
             and abs(r["python"]["person_hours"] - r["min_cost"]["person_hours"]) <= 0.1]
    summary = {
        "mode": args.mode,
        "scenarios": len(rows),
        "db_and_python_agree": f"{len(agree)}/{len(rows)}",
        "origins_per_scenario": {"villages_max": max(r["villages"] for r in rows), "townships_max": max(r["townships"] for r in rows)},
        "seconds_max": {k: max(r["seconds"][k] for r in rows) for k in ("cost_matrix", "db_solver", "python_solver")},
        "capacity_fits_demand": block([r for r in rows if r["demand_fits"]]),
        "demand_exceeds_capacity": block([r for r in rows if not r["demand_fits"]]),
        "villages_vs_township_centroids": origins_block(rows),
        "origin_to_road": {
            "villages": road_gap(repo, [(v["lat"], v["lon"]) for v in model.villages]),
            "township_centres": road_gap(repo, [(t["lat"], t["lon"]) for t in model.townships]),
        },
        "largest_gaps": [
            {"scenario": r["scenario"], "greedy_person_hours": r["greedy"]["person_hours"],
             "min_cost_person_hours": r["min_cost"]["person_hours"], "gap": round(gap(r), 4)}
            for r in sorted(rows, key=gap, reverse=True)[:3]
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    os.makedirs(RESULTS_DIR, exist_ok=True)
    out = os.path.join(RESULTS_DIR, f"{args.label}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=2)
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
