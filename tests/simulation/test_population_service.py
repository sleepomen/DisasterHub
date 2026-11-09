import json
import math

import pytest

from services.simulation.population import EVAC_RATIO, FALLBACK_DENSITY, PopulationModel, distance_km


@pytest.fixture(scope="module")
def model():
    return PopulationModel()


def test_bundled_model_loads(model):
    # 內政部 115 年 9 月的村里戶籍人口：三縣 554 個村里、41 個鄉鎮市
    assert len(model.villages) == 554
    assert len(model.townships) == 41
    assert sum(v["pop"] for v in model.villages) > 900000
    assert len(model.coastline) > 10
    assert {t["county"] for t in model.townships} == {"宜蘭縣", "花蓮縣", "臺東縣"}
    # 村里名在不同鄉鎮會重複，起點名稱帶鄉鎮才不會撞
    assert len({v["name"] for v in model.villages}) == 554
    # 海岸線由北往南排列，內插才會正確
    lats = [p[0] for p in model.coastline]
    assert lats == sorted(lats, reverse=True)


def test_coastline_interpolation(model):
    # 花蓮市（23.977, 121.601）在陸地上，外海（同緯度、經度 121.70）不是
    assert model.is_land(23.977, 121.601)
    assert not model.is_land(23.977, 121.70)
    # 超出北端 / 南端就沿用端點的經度
    assert model.coast_lon_at(26.0) == model.coastline[0][1]
    assert model.coast_lon_at(21.0) == model.coastline[-1][1]


def test_covered_villages_weighted_by_distance(model):
    covered = model.covered_villages(23.977, 121.601, 10)
    assert all(v["weight"] > 0 for v in covered)
    assert [v["weight"] for v in covered] == sorted((v["weight"] for v in covered), reverse=True)
    # 中心附近的村里不打折
    near = [v for v in covered if v["distance_km"] <= 1]
    assert near and all(v["weight"] == v["pop"] for v in near)
    towns = [t["name"] for t in model.summarize_townships(covered)]
    assert towns[0] == "花蓮市"
    assert "吉安鄉" in towns and "新城鄉" in towns
    assert "臺東市" not in towns


def test_estimate_uses_disaster_ratio(model):
    quake = model.estimate(23.977, 121.601, 10, "earthquake")
    flood = model.estimate(23.977, 121.601, 10, "flood")
    assert quake["covered_population"] == flood["covered_population"] > 150000
    assert quake["estimated_evacuees"] == round(quake["covered_population"] * EVAC_RATIO["earthquake"])
    assert flood["estimated_evacuees"] > quake["estimated_evacuees"]
    assert quake["fallback_estimate"] is False
    assert "shortfall" not in quake


def test_estimate_reports_shortfall_against_remaining(model):
    est = model.estimate(23.977, 121.601, 10, "earthquake", total_remaining=5000)
    assert est["total_remaining"] == 5000
    assert est["placeable"] == 5000
    assert est["shortfall"] == est["estimated_evacuees"] - 5000
    roomy = model.estimate(23.977, 121.601, 10, "earthquake", total_remaining=10**7)
    assert roomy["shortfall"] == 0
    assert roomy["placeable"] == roomy["estimated_evacuees"]


def test_estimate_falls_back_to_area_when_uninhabited(model):
    # 外海：沒有任何鄉鎮在圈內，用面積保底
    est = model.estimate(23.5, 122.5, 5, "fire")
    assert est["covered_population"] == 0
    assert est["fallback_estimate"] is True
    assert est["estimated_evacuees"] == round(math.pi * 25 * FALLBACK_DENSITY)
    assert est["townships"] == []


def test_missing_data_file_degrades_gracefully(tmp_path):
    model = PopulationModel(tmp_path / "missing.json", tmp_path / "missing_villages.json")
    assert model.townships == [] and model.villages == []
    assert model.is_land(23.9, 121.6)  # 沒海岸線就一律當陸地
    est = model.estimate(23.9, 121.6, 3, "flood")
    assert est["fallback_estimate"] is True


def test_custom_data_file(tmp_path):
    path = tmp_path / "pop.json"
    path.write_text(json.dumps({"coastline": [[24.0, 121.7], [23.0, 121.5]]}), encoding="utf-8")
    villages = tmp_path / "villages.json"
    villages.write_text(json.dumps({"villages": [
        {"county": "花蓮縣", "township": "甲鎮", "village": "東里", "lat": 23.5, "lon": 121.4, "pop": 1000},
        {"county": "花蓮縣", "township": "甲鎮", "village": "西里", "lat": 23.5, "lon": 121.3, "pop": 3000},
    ]}), encoding="utf-8")
    model = PopulationModel(path, villages)
    # 鄉鎮由村里加總，位置依人口加權
    town = model.to_dict()["townships"][0]
    assert town["name"] == "甲鎮" and town["pop"] == 4000 and town["lon"] == pytest.approx(121.325)
    assert model.coast_lon_at(23.5) == pytest.approx(121.6)
    est = model.estimate(23.5, 121.35, 50, "earthquake")
    assert est["covered_population"] == 4000
    assert [v["name"] for v in est["villages"]] == ["甲鎮西里", "甲鎮東里"]
    assert est["townships"] == [{"name": "甲鎮", "county": "花蓮縣", "weight": 4000}]


def test_distance_km_matches_haversine():
    assert distance_km(23.977, 121.601, 23.977, 121.601) == 0
    assert distance_km(23.977, 121.601, 22.756, 121.144) == pytest.approx(143.6, abs=1.0)


def test_locate_names_a_picked_point_after_the_nearest_village(model):
    """地圖選點的標籤：最近的村里起點在 5 公里內就冠它的鄉鎮與村里，海上不冠"""
    place = model.locate(24.86, 121.83)
    assert (place["township"], place["village"], place["land"]) == ("頭城鎮", "武營里", True)
    sea = model.locate(23.0, 122.0)
    assert sea["land"] is False and sea["township"] == "" and sea["distance_km"] > 5


def test_village_origins_carry_english_names(model):
    names = {v["name"]: v["name_en"] for v in model.villages}
    assert names["壽豐鄉豐裡村"] == "Fengli Village, Shoufeng Township"
