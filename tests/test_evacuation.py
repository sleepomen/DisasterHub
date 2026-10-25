from models.shelter import Shelter
from services.evacuation import (
    DETOUR_FACTOR,
    FALLBACK_ORIGIN,
    Origin,
    apportion,
    origins_from,
    plan_evacuation,
    split_hazard_hits,
    undesignated,
)

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


def test_road_distance_decides_who_is_nearest():
    """直線最近的那間要繞山：道路距離比較短的先填"""
    origin = Origin("南澳鄉", 24.40, 121.75, 200)
    plan = plan_evacuation([origin], [NEAR, FAR], {(0, 0): 25600.0, (0, 1): 15550.0})
    assert [f["to"] for f in plan["flows"]] == ["[HUALIEN] 遠"]
    assert plan["flows"][0]["road_km"] == 15.55 and plan["flows"][0]["distance_km"] > 0
    assert plan["distance_basis"] == "road"


def test_unrouted_pairs_are_estimated_with_the_detour_factor():
    """
    算不出道路距離的配對不能拿直線距離跟道路距離比（直線一定比較短，會被排到前面）：
    近的那間直線 10 公里、估算 11.5 公里，輸給道路 11 公里的遠的那間
    """
    origin = Origin("甲", 24.0, 121.6, 600)
    near = Shelter(name="直線近", capacity=100, lat=24.0 + 10 / 111.2, lon=121.6)
    far = Shelter(name="道路近", capacity=100, lat=24.0 - 10.5 / 111.2, lon=121.6)
    plan = plan_evacuation([origin], [near, far], {(0, 1): 11000.0})
    assert [(f["to"], f["road_km"]) for f in plan["flows"]] == [("道路近", 11.0), ("直線近", None)]
    assert plan["distance_basis"] == "mixed"
    assert 1 < DETOUR_FACTOR < 1.5


def test_no_road_distances_falls_back_to_straight_line():
    plan = plan_evacuation([Origin("花蓮市", 23.977, 121.601, 50)], [NEAR, FAR], {})
    assert plan["distance_basis"] == "straight"
    assert [f["road_km"] for f in plan["flows"]] == [None]


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


def test_split_hazard_hits_separates_inside_from_near():
    inside, near = split_hazard_hits({
        # 在區域內，旁邊另一個區域只是靠近：只列所在的那個
        "[YILAN] 澳花國小": [("宜縣DF139", 0.0), ("宜縣DF140", 80.0)],
        "[YILAN] 南山國小": [("宜縣DF079", 121.4), ("宜縣DF078", 180.2)],
    })
    assert inside == {"[YILAN] 澳花國小": ["宜縣DF139"]}
    assert near == {"[YILAN] 南山國小": {"refs": ["宜縣DF079", "宜縣DF078"], "distance_m": 121}}
    assert split_hazard_hits({}) == ({}, {})


def test_undesignated_shelters_follow_the_official_hazard_types():
    quake_only = Shelter(name="只收震災", capacity=50, lat=24.0, lon=121.6, hazards=("震災",))
    both = Shelter(name="都收", capacity=50, lat=24.0, lon=121.6, hazards=("水災", "震災"))
    blank = Shelter(name="沒填", capacity=50, lat=24.0, lon=121.6)
    assert undesignated([quake_only, both, blank], "flood") == ["只收震災"]
    # 類別沒填不代表不適用；火災沒有對應類別
    assert undesignated([quake_only, both, blank], "earthquake") == []
    assert undesignated([quake_only], "fire") == []
