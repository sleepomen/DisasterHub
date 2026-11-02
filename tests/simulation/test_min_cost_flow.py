"""疏散分配的最小成本流：先安置最多的人，同樣人數下讓所有人的時間加總最短"""
import itertools
import random

from models.shelter import Shelter
from services.simulation.evacuation import Origin, plan_evacuation
from services.simulation.min_cost_flow import min_cost_flow


def brute_force(supply, capacity, cost):
    """所有整數分配裡，先比安置人數、再比總成本"""
    pairs = sorted(cost)
    best = None
    for combo in itertools.product(*(range(min(supply[i], capacity[j]) + 1) for i, j in pairs)):
        if any(sum(x for (i, _), x in zip(pairs, combo, strict=True) if i == a) > supply[a] for a in range(len(supply))):
            continue
        if any(sum(x for (_, j), x in zip(pairs, combo, strict=True) if j == b) > capacity[b] for b in range(len(capacity))):
            continue
        key = (-sum(combo), sum(x * cost[p] for p, x in zip(pairs, combo, strict=True)))
        best = key if best is None or key < best else best
    return best


def test_matches_brute_force_on_small_random_cases():
    rng = random.Random(20261102)
    for _ in range(200):
        supply = [rng.randint(0, 3) for _ in range(rng.randint(1, 3))]
        capacity = [rng.randint(0, 3) for _ in range(rng.randint(1, 3))]
        cost = {(i, j): rng.randint(1, 9) for i in range(len(supply)) for j in range(len(capacity)) if rng.random() < 0.8}
        moved = min_cost_flow(supply, capacity, cost)
        assert (-sum(moved.values()), sum(x * cost[p] for p, x in moved.items())) == brute_force(supply, capacity, cost)


def test_beats_nearest_first_when_it_matters():
    """
    甲離 A 1 分、離 B 2 分；乙離 A 2 分、離 B 10 分；A、B 各 1 個空位。
    最近優先先把甲放進 A（1 分），乙只能去 B（10 分），合計 11 分；最佳是甲去 B、乙去 A，合計 4 分
    """
    moved = min_cost_flow([1, 1], [1, 1], {(0, 0): 1, (0, 1): 2, (1, 0): 2, (1, 1): 10})
    assert moved == {(0, 1): 1, (1, 0): 1}


def test_places_as_many_people_as_possible_first():
    # 只有一個空位：放進去的是成本低的那個人，另一個算無法安置
    assert min_cost_flow([1, 1], [1], {(0, 0): 5, (1, 0): 3}) == {(1, 0): 1}


def test_plan_uses_the_optimal_assignment():
    origins = [Origin("甲", 24.0, 121.6, 1), Origin("乙", 24.0, 121.7, 1)]
    shelters = [Shelter("A", 1, 24.01, 121.6, 0), Shelter("B", 1, 24.02, 121.6, 0)]
    road = {(0, 0): 60.0, (0, 1): 120.0, (1, 0): 120.0, (1, 1): 600.0}
    plan = plan_evacuation(origins, shelters, road)
    assert sorted((f["from"], f["to"]) for f in plan["flows"]) == [("乙", "A"), ("甲", "B")]
    assert plan["placed"] == 2 and plan["unplaced"] == 0
