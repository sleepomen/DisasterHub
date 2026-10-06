import json
import math

import pytest

from services.population_service import EVAC_RATIO, FALLBACK_DENSITY, PopulationModel, distance_km


@pytest.fixture(scope="module")
def model():
    return PopulationModel()


def test_bundled_model_loads(model):
    assert len(model.townships) == 29
    assert len(model.coastline) > 10
    assert {t["county"] for t in model.townships} == {"宜蘭縣", "花蓮縣", "台東縣"}
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


def test_covered_townships_weighted_by_distance(model):
    covered = model.covered_townships(23.977, 121.601, 10)
    names = [t["name"] for t in covered]
    assert names[0] == "花蓮市"
    assert "吉安鄉" in names and "新城鄉" in names
    assert "台東市" not in names
    # 中心點所在鄉鎮不打折
    assert covered[0]["weight"] == 99000
    assert all(t["weight"] > 0 for t in covered)
    assert [t["weight"] for t in covered] == sorted((t["weight"] for t in covered), reverse=True)


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
    model = PopulationModel(tmp_path / "missing.json")
    assert model.townships == []
    assert model.is_land(23.9, 121.6)  # 沒海岸線就一律當陸地
    est = model.estimate(23.9, 121.6, 3, "flood")
    assert est["fallback_estimate"] is True


def test_custom_data_file(tmp_path):
    path = tmp_path / "pop.json"
    path.write_text(json.dumps({
        "coastline": [[24.0, 121.7], [23.0, 121.5]],
        "townships": [{"name": "甲鎮", "county": "花蓮縣", "lat": 23.5, "lon": 121.4, "pop": 1000}],
    }), encoding="utf-8")
    model = PopulationModel(path)
    assert model.to_dict()["townships"][0]["name"] == "甲鎮"
    assert model.coast_lon_at(23.5) == pytest.approx(121.6)
    est = model.estimate(23.5, 121.4, 5, "earthquake")
    assert est["covered_population"] == 1000


def test_distance_km_matches_haversine():
    assert distance_km(23.977, 121.601, 23.977, 121.601) == 0
    assert distance_km(23.977, 121.601, 22.756, 121.144) == pytest.approx(143.6, abs=1.0)
