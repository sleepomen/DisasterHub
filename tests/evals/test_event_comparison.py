import json
import pathlib

import pytest

from evals.run_event_comparison import compare, load_shelters, render, shelter_coverage, zone_population

ROOT = pathlib.Path(__file__).resolve().parents[2]
EVENT_DIR = ROOT / "evals" / "datasets" / "events"

ZONE = {"光復鄉": ["大馬村", "東富村"]}
VILLAGES = [
    {"township": "光復鄉", "village": "大馬村", "pop": 1000},
    {"township": "光復鄉", "village": "東富村", "pop": 1000},
    {"township": "光復鄉", "village": "大進村", "pop": 5000},
]
SHELTERS = [
    {"name": "光復國中", "township": "光復鄉", "village": "大馬村", "total_vessel": 100, "hazards": ["水災"]},
    {"name": "東富活動中心", "township": "光復鄉", "village": "東富村", "total_vessel": 50, "hazards": ["土石流"]},
    {"name": "大進活動中心", "township": "光復鄉", "village": "大進村", "total_vessel": 80, "hazards": ["水災"]},
]


def event(shelters=None):
    return {
        "id": "t", "name": "測試", "date": "2025-09-23", "hazard": "flood",
        "warning_zone": {"villages": ZONE},
        "evacuation": {"evacuated": 1000, "vertical": 800},
        "sheltered": [
            {"as_of": "9/24", "total": 100, "shelters": shelters or [
                {"name": "大進國小", "township": "光復鄉", "people": 90, "dataset_name": None},
                {"name": "光復國中", "township": "光復鄉", "people": 10, "dataset_name": "光復國中"},
            ]},
            {"as_of": "9/24 晚", "total": 200, "at_least": True, "shelters": []},
        ],
    }


def test_demand_compares_assumed_ratio_with_actual_shares():
    summary = compare(event(), SHELTERS, VILLAGES, 0.22)
    d = summary["demand"]
    # 只算警戒區的兩個村里，大進村不在裡面
    assert summary["zone"]["population"] == 2000
    assert d["system_evacuees"] == 440
    assert d["left_home"] == 200
    assert d["share"]["sheltered_low"] == 0.05 and d["share"]["sheltered_high"] == 0.1
    assert d["overestimate"] == [2.2, 4.4]


def test_coverage_counts_people_in_shelters_missing_from_the_dataset():
    c = compare(event(), SHELTERS, VILLAGES, 0.22)["coverage"]
    assert c["people_not_in_dataset"] == 90
    assert c["share_not_in_dataset"] == 0.9


def test_zone_shelters_skip_ones_not_designated_for_the_hazard():
    z = compare(event(), SHELTERS, VILLAGES, 0.22)["zone_shelters"]
    # 東富活動中心只列土石流，不算淹水能去的；大進活動中心不在警戒區
    assert [r["name"] for r in z["shelters"]] == ["光復國中", "東富活動中心"]
    assert z["designated"] == 1 and z["designated_capacity"] == 100


def test_report_scenarios_list_used_shelters_inside_the_zone():
    report = {"scenarios": [
        {"township": "光復鄉", "kind": "flood", "radius_km": 10, "covered_population": 7000,
         "evacuees": {"1.0": 1540}, "used": ["[HUALIEN] 光復國中", "[HUALIEN] 大進活動中心"]},
        {"township": "光復鄉", "kind": "earthquake", "radius_km": 10, "covered_population": 7000,
         "evacuees": {"1.0": 840}, "used": ["[HUALIEN] 光復國中"]},
    ]}
    scenarios = compare(event(), SHELTERS, VILLAGES, 0.22, report)["zone_shelters"]["scenarios"]
    assert len(scenarios) == 1
    assert scenarios[0]["used_in_zone"] == ["光復國中"]


def test_mapping_must_name_a_real_shelter():
    bad = [{"name": "鳳林樂活會館", "township": "鳳林鎮", "people": 1, "dataset_name": "不存在的會館"}]
    with pytest.raises(SystemExit):
        shelter_coverage({"shelters": bad}, SHELTERS)


def test_null_mapping_is_rejected_when_the_dataset_has_that_name():
    bad = [{"name": "光復國中", "township": "光復鄉", "people": 1, "dataset_name": None}]
    with pytest.raises(SystemExit):
        shelter_coverage({"shelters": bad}, SHELTERS)


def test_unknown_zone_village_is_an_error_not_a_smaller_population():
    assert zone_population(VILLAGES, {"光復鄉": ["不存在村"]})["missing"] == ["光復鄉不存在村"]
    bad = event()
    bad["warning_zone"]["villages"] = {"光復鄉": ["大馬村", "不存在村"]}
    with pytest.raises(SystemExit):
        compare(bad, SHELTERS, VILLAGES, 0.22)


def test_render_marks_an_open_ended_count():
    text = render(compare(event(), SHELTERS, VILLAGES, 0.22))
    assert "100～200+" in text


@pytest.mark.parametrize("path", sorted(EVENT_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_event_files_hold_up_against_the_real_data(path):
    """每個事件檔：每個數字都有出處，收容處所的對應、警戒區村里都對得上真的資料"""
    ev = json.loads(path.read_text(encoding="utf-8"))
    assert ev["evacuation"]["source"]["url"].startswith("https://")
    assert all(s["source"]["url"].startswith("https://") for s in ev["sheltered"])
    villages = json.loads((ROOT / "data_reference" / "east_villages.json").read_text(encoding="utf-8"))["villages"]
    summary = compare(ev, load_shelters(str(ROOT / "data_for_refuge")), villages, 0.22)
    assert summary["zone"]["villages"] == sum(len(v) for v in ev["warning_zone"]["villages"].values())
