"""
疏散分配評測：最小成本流（現在用的）跟「全域最近優先」貪婪法（以前用的）比，差多少。

情境是每個鄉鎮人口重心當模擬中心、半徑 5 與 10 公里、強震與淹水（依適用災害排除避難所），
起點、避難所與每一組的通行成本都跟 /api/simulate_disaster 一樣算（services/simulation/evacuation.pair_costs）。
比的是：安置人數、所有人的通行時間加總、每人平均分鐘、最遠的那一趟。

貪婪法只留在這裡當基準：所有配對依時間排序，依序把鄉鎮剩下的人塞進避難所剩下的空位。

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
from services.simulation.evacuation import origins_from, pair_costs, undesignated  # noqa: E402
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


def scenarios(repo, model, mode: str):
    for town in model.townships:
        for radius in RADII:
            for kind in KINDS:
                impacted = repo.get_shelters_in_radius(town["lat"], town["lon"], radius)
                skip = set(undesignated(impacted, kind))
                shelters = [s for s in impacted if s.name not in skip]
                if not shelters:
                    continue
                population = model.estimate(town["lat"], town["lon"], radius, kind, sum(s.remaining for s in shelters))
                origins = origins_from(population, town["lat"], town["lon"])
                road = evacuation_road_distances(repo, origins, shelters, None, mode)
                cost = {pair: c for pair, (c, _, _) in pair_costs(origins, shelters, road, mode).items()}
                yield f"{town['name']} {radius} km {kind}", origins, shelters, cost


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("walk", "drive"), default="walk")
    parser.add_argument("--label", default="assignment_eval")
    args = parser.parse_args()

    repo, model = ShelterRepository(), PopulationModel()
    rows = []
    for name, origins, shelters, cost in scenarios(repo, model, args.mode):
        supply = [o.people for o in origins]
        capacity = [max(0, int(s.remaining)) for s in shelters]
        started = time.monotonic()
        optimal = min_cost_flow(supply, capacity, cost)
        seconds = time.monotonic() - started
        rows.append({
            "scenario": name, "origins": len(origins), "shelters": len(shelters),
            "demand_fits": sum(supply) <= sum(capacity), "solve_seconds": round(seconds, 4),
            "greedy": measure(greedy(supply, capacity, cost), cost), "min_cost": measure(optimal, cost),
        })

    def gap(r):
        g, m = r["greedy"]["person_hours"], r["min_cost"]["person_hours"]
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

    summary = {
        "mode": args.mode,
        "capacity_fits_demand": block([r for r in rows if r["demand_fits"]]),
        "demand_exceeds_capacity": block([r for r in rows if not r["demand_fits"]]),
        "solve_seconds_max": max(r["solve_seconds"] for r in rows),
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
