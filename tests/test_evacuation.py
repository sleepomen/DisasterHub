from models.shelter import Shelter
from services.evacuation import FALLBACK_ORIGIN, Origin, apportion, origins_from, plan_evacuation

NEAR = Shelter(name="[HUALIEN] 近", capacity=100, lat=23.98, lon=121.60, current_people=40)   # 剩 60
FAR = Shelter(name="[HUALIEN] 遠", capacity=500, lat=24.10, lon=121.60, current_people=0)     # 剩 500
FULL = Shelter(name="[HUALIEN] 滿", capacity=50, lat=23.97, lon=121.60, current_people=50)    # 剩 0


def test_apportion_sums_exactly_to_the_total():
    # 直接四捨五入：100 * 1/3 三份都是 33，加起來少 1 人，跟「預估需疏散 100 人」對不上
    shares = apportion(100, {"甲": 1, "乙": 1, "丙": 1})
    assert sum(shares.values()) == 100
    assert sorted(shares.values()) == [33, 33, 34]
    assert apportion(0, {"甲": 5}) == {"甲": 0}


def test_nearest_shelter_fills_first_and_capacity_is_never_exceeded():
    origin = Origin("花蓮市", 23.977, 121.601, 200)
    plan = plan_evacuation([origin], [FAR, NEAR, FULL])
    assert plan["assigned"] == {"[HUALIEN] 近": 60, "[HUALIEN] 遠": 140}
    assert [f["to"] for f in plan["flows"]] == ["[HUALIEN] 近", "[HUALIEN] 遠"]
    assert (plan["placed"], plan["unplaced"]) == (200, 0)


def test_overflow_is_reported_as_unplaced():
    plan = plan_evacuation([Origin("花蓮市", 23.977, 121.601, 700)], [NEAR, FAR])
    assert (plan["placed"], plan["unplaced"]) == (560, 140)


def test_plan_is_deterministic():
    """以前前端用亂數分配，同一組參數每次結果都不一樣；後端的分配必須可重現"""
    origins = [Origin("甲", 23.99, 121.60, 90), Origin("乙", 24.05, 121.60, 90)]
    first = plan_evacuation(origins, [NEAR, FAR])
    assert all(plan_evacuation(origins, [FAR, NEAR]) == first for _ in range(5))


def test_origins_follow_population_weights():
    population = {
        "estimated_evacuees": 100,
        "townships": [
            {"name": "花蓮市", "lat": 23.98, "lon": 121.60, "weight": 300},
            {"name": "吉安鄉", "lat": 23.96, "lon": 121.57, "weight": 100},
        ],
    }
    origins = origins_from(population, 23.97, 121.6)
    assert [(o.name, o.people) for o in origins] == [("花蓮市", 75), ("吉安鄉", 25)]


def test_no_settlement_starts_from_the_disaster_center():
    origins = origins_from({"estimated_evacuees": 30, "townships": []}, 23.5, 121.5)
    assert [(o.name, o.lat, o.lon, o.people) for o in origins] == [(FALLBACK_ORIGIN, 23.5, 121.5, 30)]
    assert origins_from({"estimated_evacuees": 0, "townships": []}, 23.5, 121.5) == []
