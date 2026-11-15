"""容量缺口報告：鄉鎮自有容量、情境摘要、避難所使用情形、報告文字，以及跟模擬 API 共用的目的地篩選"""
from unittest.mock import MagicMock

from models.shelter import Shelter
from services.simulation.capacity_report import (
    cut_off_villages,
    nobody_placed,
    render_markdown,
    shelter_usage,
    summarize_scenario,
    township_capacity,
)
from services.simulation.evacuation import Origin
from services.simulation.scenario import destinations_for
from tools.capacity_report import with_people


def shelter(name, capacity, township="甲鄉", hazards=("水災", "震災"), vulnerable=True):
    return Shelter(f"[HUALIEN] {name}", capacity, 23.9, 121.6, township=township, hazards=hazards, vulnerable_ok=vulnerable)


TOWNS = [
    {"name": "甲鄉", "county": "花蓮縣", "pop": 10000, "lat": 23.9, "lon": 121.6},
    {"name": "乙鄉", "county": "宜蘭縣", "pop": 5000, "lat": 24.7, "lon": 121.7},
]


def test_township_capacity_counts_only_shelters_usable_for_each_hazard():
    shelters = [
        shelter("一號", 300),
        shelter("只收水災", 200, hazards=("水災", "土石流"), vulnerable=False),
        shelter("潛勢範圍內", 100),
        shelter("沒填類別", 50, hazards=()),  # 來源沒填不代表不適用
        shelter("零容量", 0),
        shelter("乙鄉國小", 400, township="乙鄉", hazards=("水災",)),
    ]
    rows = township_capacity(TOWNS, shelters, {"flood": {"[HUALIEN] 潛勢範圍內"}})
    # 宜蘭縣排在花蓮縣前面
    assert [r["township"] for r in rows] == ["乙鄉", "甲鄉"]
    town = rows[1]
    assert (town["shelters"], town["capacity"], town["vulnerable_capacity"]) == (5, 650, 450)
    assert town["hazards"]["earthquake"] == {"shelters": 4, "capacity": 450, "share": 0.045}
    assert town["hazards"]["flood"] == {"shelters": 4, "capacity": 550, "share": 0.055}
    assert town["zero_capacity"] == ["[HUALIEN] 零容量"]
    assert rows[0]["hazards"]["earthquake"] == {"shelters": 0, "capacity": 0, "share": 0.0}


def plan(placed, unplaced, assigned=None, cut_off=(), unreachable=()):
    return {"placed": placed, "unplaced": unplaced, "assigned": assigned or {}, "cut_off": list(cut_off),
            "unreachable": list(unreachable), "distance_basis": "road"}


POPULATION = {
    "covered_population": 10000, "evacuation_ratio": 0.12,
    "villages": [{"name": "甲鄉一村", "weight": 6000}, {"name": "甲鄉二村", "weight": 4000}],
}


def test_summarize_scenario_gives_the_unplaced_range_and_the_ceiling():
    dest = [shelter("大", 500), shelter("小", 100), shelter("遠", 200)]
    plans = {
        0.8: plan(800, 160), 1.0: plan(800, 400, {"[HUALIEN] 大": 500, "[HUALIEN] 小": 100, "[HUALIEN] 遠": 0}),
        1.2: plan(800, 640), "all": plan(800, 9200, cut_off=["甲鄉二村"], unreachable=["[HUALIEN] 遠"]),
    }
    sc = summarize_scenario(TOWNS[0], "flood_red", 10, POPULATION, dest, plans, excluded=1, alerts=3)
    assert sc["evacuees"] == {"0.8": 960, "1.0": 1200, "1.2": 1440}
    assert sc["unplaced"] == {"0.8": 160, "1.0": 400, "1.2": 640}
    assert (sc["placed_max"], sc["max_ratio"]) == (800, 0.08)
    assert sc["cut_off"] == [{"village": "甲鄉二村", "population": 4000}]
    assert sc["full"] == ["[HUALIEN] 大", "[HUALIEN] 小"] and sc["unused"] == ["[HUALIEN] 遠"]
    assert sc["used"] == ["[HUALIEN] 大", "[HUALIEN] 小"]
    assert (sc["capacity"], sc["excluded"], sc["red_alerts"]) == (800, 1, 3)


def test_a_circle_without_any_usable_shelter_leaves_everyone_unplaced():
    plans = {f: nobody_placed(round(1200 * f)) for f in (0.8, 1.0, 1.2)} | {"all": nobody_placed(10000)}
    sc = summarize_scenario(TOWNS[0], "earthquake", 10, POPULATION, [], plans)
    assert sc["unplaced"] == {"0.8": 960, "1.0": 1200, "1.2": 1440}
    assert sc["max_ratio"] == 0 and sc["destinations"] == 0


def scenario(town, kind, full=(), used=(), unused=(), cut_off=()):
    return {"township": town, "kind": kind, "full": list(full), "used": list(used), "unused": list(unused),
            "cut_off": [{"village": v, "population": p} for v, p in cut_off]}


def test_shelter_usage_ignores_red_alert_runs_and_separates_never_used():
    usage = shelter_usage([
        scenario("甲鄉", "earthquake", full=["甲"], used=["甲"], unused=["乙"]),
        scenario("甲鄉", "flood", used=["乙"], unused=["丙"]),
        scenario("甲鄉", "flood_red", full=["丙"], used=["丙"]),
    ])
    assert usage["甲"] == {"used": 1, "full": 1, "unused": 0}
    # 乙在一個情境沒分到人、另一個有：不算「從來沒分到人」
    assert usage["乙"] == {"used": 1, "full": 0, "unused": 1}
    assert usage["丙"] == {"used": 0, "full": 0, "unused": 1}


def test_cut_off_villages_count_each_village_once():
    runs = [scenario("甲鄉", "flood_red", cut_off=[("甲鄉一村", 300)]),
            scenario("乙鄉", "flood_red", cut_off=[("甲鄉一村", 500), ("乙鄉一村", 20)]),
            scenario("乙鄉", "flood", cut_off=[("不算", 999)])]
    assert cut_off_villages(runs) == {"甲鄉一村": 500, "乙鄉一村": 20}


def full_scenario(town, kind, destinations=3, unplaced=(0, 100, 200), cut_off=()):
    return {
        "township": town, "county": "花蓮縣", "kind": kind, "radius_km": 10, "covered_population": 9000,
        "destinations": destinations, "unplaced": dict(zip(("0.8", "1.0", "1.2"), unplaced, strict=True)),
        "max_ratio": 0.05 if destinations else 0, "full": [], "used": [], "unused": [],
        "cut_off": [{"village": v, "population": p} for v, p in cut_off],
    }


def test_markdown_report_states_the_assumptions_and_flags_data_to_check():
    towns = township_capacity(TOWNS, [shelter("一號", 300, hazards=("水災",)), shelter("乙鄉國小", 0, township="乙鄉")])
    report = {
        "meta": {"generated_at": "2026-11-14 10:00", "shelter_dataset_updated": "2025-12-16", "shelters": 2,
                 "population_month": "民國 115 年 9 月", "villages": 2, "roads_loaded": "2026-11-02", "debris_zones": 1,
                 "evacuation_ratios": {"earthquake": 0.12, "flood": 0.22}},
        "townships": towns,
        "scenarios": [
            full_scenario("甲鄉", "earthquake", destinations=0, unplaced=(960, 1200, 1440)),
            full_scenario("甲鄉", "flood"),
            full_scenario("甲鄉", "flood_red", cut_off=[("甲鄉一村", 300)]),
        ],
        "shelter_usage": {},
    }
    text = render_markdown(report)
    assert "**規劃參考**" in text and "民國 115 年 9 月" in text
    assert "三縣登記容量合計 300 人，是戶籍人口 15,000 人的 **2.0%**" in text
    assert "**1 個鄉鎮沒有任何一間避難所列為震災適用**：甲鄉（10,000 人）" in text
    assert "| 甲鄉 | 9,000 | 全部 1,200（960～1,440） | 0%（沒有能去的避難所） | 100（0～200） | 5.0% | 1 村里，300 人 |" in text
    assert "| 乙鄉 | — | — | — | — | — | —（無潛勢溪流） |" in text
    assert "會讓 1 個村里完全沒有安全道路" in text


def test_destinations_skip_hazard_zones_and_undesignated_shelters():
    impacted = [shelter("安全", 100), shelter("範圍內", 100), shelter("靠近", 100), shelter("只收震災", 100, hazards=("震災",))]
    repo = MagicMock()
    repo.shelter_hazard_distances.return_value = {
        "[HUALIEN] 範圍內": [("花縣DF001", 0.0)], "[HUALIEN] 靠近": [("花縣DF002", 120.4)],
    }
    chosen = destinations_for(repo, impacted, "flood")
    assert [s.name for s in chosen.shelters] == ["[HUALIEN] 安全", "[HUALIEN] 靠近"]
    assert chosen.excluded == {"[HUALIEN] 範圍內": ["花縣DF001"]}
    assert chosen.near == {"[HUALIEN] 靠近": {"refs": ["花縣DF002"], "distance_m": 120}}
    assert chosen.undesignated == ["[HUALIEN] 只收震災"] and chosen.hazard == "debris_flow"
    # 地震沒有對應的潛勢圖資，不查
    repo.reset_mock()
    assert len(destinations_for(repo, impacted, "earthquake").shelters) == 4
    repo.shelter_hazard_distances.assert_not_called()


def test_destinations_fall_back_to_no_exclusion_when_the_hazard_query_fails():
    repo = MagicMock()
    repo.shelter_hazard_distances.side_effect = RuntimeError("db down")
    chosen = destinations_for(repo, [shelter("安全", 100)], "flood")
    assert [s.name for s in chosen.shelters] == ["[HUALIEN] 安全"] and chosen.excluded == {}


def test_with_people_drops_empty_origins_and_renumbers_road_costs():
    origins = [Origin("一村", 0, 0, 0), Origin("二村", 0, 0, 0), Origin("三村", 0, 0, 0)]
    road = {(0, 0): 10.0, (1, 0): 20.0, (2, 0): None, (2, 1): 30.0}
    kept, costs = with_people(origins, road, {"二村": 5, "三村": 7})
    assert [(o.name, o.people) for o in kept] == [("二村", 5), ("三村", 7)]
    assert costs == {(0, 0): 20.0, (1, 0): None, (1, 1): 30.0}
