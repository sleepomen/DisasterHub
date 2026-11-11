"""
疏散分配的敏感度分析：模型的假設不準時，結論會不會變。

README 把「疏散是模型，不是計畫」列為限制：人口是戶籍人口、每個村里只有一個起點、避難所容量照登記的算。
這裡用疏散分配評測的同一組情境（evals/run_assignment_eval.py：41 個鄉鎮 × 半徑 5、10 公里 × 強震、淹水，步行），
每次只改一個假設，跟原本的結果比：

    人數 ×0.8、×1.2         戶籍人口跟當下實際在那裡的人不同
    容量 50%、75%           避難所受損、部分空間不能用
    起點隨機移動 0.5～1 公里  人不一定都在村里最大的聚落（兩組亂數）

比的是：未安置人數、用到的避難所集合有多少重疊（Jaccard）、每人平均通行時間、統一調度比各自就近前往省多少。
不改速度：步行時所有成本等比例縮放，最佳分配完全不變，量了也沒有意義。
人數與容量的改動沿用同一份路網距離，只有起點位移要重算。

    docker exec disaster_app python evals/run_sensitivity_eval.py
"""
import json
import math
import os
import random
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from evals.run_assignment_eval import KINDS, RADII, greedy, problem  # noqa: E402
from repositories.shelter_repository import ShelterRepository  # noqa: E402
from services.simulation.evacuation import Origin, origins_from, undesignated  # noqa: E402
from services.simulation.population import PopulationModel  # noqa: E402

RESULTS = os.path.join(ROOT, "evals", "results", "sensitivity_eval.json")
MODE = "walk"
DEMAND = (0.8, 1.2)
CAPACITY = (0.5, 0.75)
JITTER_KM = (0.5, 1.0)
JITTER_SEEDS = (1, 2)


def jittered(origins: list[Origin], seed: int) -> list[Origin]:
    """每個起點往隨機方向移動 0.5～1 公里（同一個 seed 結果固定）"""
    rng = random.Random(seed)
    out = []
    for o in origins:
        angle, km = rng.uniform(0, 2 * math.pi), rng.uniform(*JITTER_KM)
        lat = o.lat + km * math.cos(angle) / 111.32
        lon = o.lon + km * math.sin(angle) / (111.32 * math.cos(math.radians(o.lat)))
        out.append(Origin(o.name, lat, lon, o.people))
    return out


def outcome(repo, supply, capacity, cost) -> dict:
    flows = repo.assignment_flows(supply, capacity, cost)
    nearest = greedy(supply, capacity, cost)
    placed = sum(flows.values())
    total = sum(p * cost[pair] for pair, p in flows.items())
    nearest_total = sum(p * cost[pair] for pair, p in nearest.items())
    return {
        "evacuees": sum(supply),
        "placed": placed,
        "unplaced": sum(supply) - placed,
        "shelters": sorted({j for (_, j) in flows}),
        "minutes_per_person": total / placed / 60 if placed else 0.0,
        "coordination_gain": (nearest_total - total) / nearest_total if nearest_total else 0.0,
    }


def jaccard(a, b) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 1.0


def main() -> int:
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
                origins = origins_from(population, town["lat"], town["lon"])
                if not origins:
                    continue
                supply, capacity, cost = problem(repo, origins, shelters, MODE)
                variants = {"baseline": outcome(repo, supply, capacity, cost)}
                for factor in DEMAND:
                    scaled = origins_from({**population, "estimated_evacuees": round(population["estimated_evacuees"] * factor)},
                                          town["lat"], town["lon"])
                    people = {o.name: o.people for o in scaled}
                    variants[f"demand x{factor}"] = outcome(repo, [people.get(o.name, 0) for o in origins], capacity, cost)
                for factor in CAPACITY:
                    variants[f"capacity {int(factor * 100)}%"] = outcome(repo, supply, [int(c * factor) for c in capacity], cost)
                for seed in JITTER_SEEDS:
                    s2, c2, cost2 = problem(repo, jittered(origins, seed), shelters, MODE)
                    variants[f"origins moved (seed {seed})"] = outcome(repo, s2, c2, cost2)
                rows.append({"scenario": f"{town['name']} {radius} km {kind}", "evacuees": sum(supply),
                             "capacity": sum(capacity), "variants": variants})
        print(f"{town['name']} 完成，累計 {len(rows)} 個情境", flush=True)

    def block(name):
        base = [r["variants"]["baseline"] for r in rows]
        var = [r["variants"][name] for r in rows]
        unplaced_base, unplaced_var = sum(b["unplaced"] for b in base), sum(v["unplaced"] for v in var)
        overlap = [jaccard(b["shelters"], v["shelters"]) for b, v in zip(base, var, strict=True)]
        times = [(v["minutes_per_person"] - b["minutes_per_person"]) / b["minutes_per_person"]
                 for b, v in zip(base, var, strict=True) if b["minutes_per_person"] and v["minutes_per_person"]]
        return {
            "evacuees_total": sum(v["evacuees"] for v in var),
            # 分母是這個改動之後的疏散人數（人數 ×1.2 時分母也跟著變大）
            "unplaced_share": round(unplaced_var / sum(v["evacuees"] for v in var), 4),
            "unplaced_total": unplaced_var, "unplaced_change": unplaced_var - unplaced_base,
            "shelter_set_jaccard_mean": round(statistics.mean(overlap), 3),
            "shelter_set_jaccard_min": round(min(overlap), 3),
            "minutes_per_person_change_mean": round(statistics.mean(times), 4) if times else None,
            "coordination_gain_mean": round(statistics.mean(v["coordination_gain"] for v in var), 4),
            "coordination_gain_max": round(max(v["coordination_gain"] for v in var), 4),
            "scenarios_where_coordination_helps": sum(v["coordination_gain"] > 1e-6 for v in var),
        }

    names = list(rows[0]["variants"])
    summary = {"mode": MODE, "scenarios": len(rows), "evacuees_total": sum(r["evacuees"] for r in rows),
               "variants": {name: block(name) for name in names}}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    with open(RESULTS, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=1)
    print(f"saved {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
