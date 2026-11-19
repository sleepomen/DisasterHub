"""容量缺口報告的網頁：/report 頁面、/api/capacity_report 只回網頁要的部分，報告檔更新就重讀"""
import json
import os

import pytest

from api import report as report_api
from services.simulation.capacity_report import page_view
from tests.web.test_api_data import client  # noqa: F401  共用同一個假資料庫的 app


def town(name, county, pop, capacity, eq_shelters=1):
    share = round(capacity / pop, 4)
    return {
        "county": county, "township": name, "population": pop, "shelters": 2, "capacity": capacity,
        "vulnerable_capacity": capacity, "zero_capacity": [],
        "hazards": {"earthquake": {"shelters": eq_shelters, "capacity": capacity if eq_shelters else 0,
                                   "share": share if eq_shelters else 0.0},
                    "flood": {"shelters": 2, "capacity": capacity, "share": share}},
    }


def scenario(name, kind, cut_off=()):
    return {
        "township": name, "county": "花蓮縣", "kind": kind, "radius_km": 10, "covered_population": 9000,
        "destinations": 3, "unplaced": {"0.8": 0, "1.0": 100, "1.2": 200}, "max_ratio": 0.05,
        "cut_off": [{"village": v, "population": p} for v, p in cut_off], "unreachable": ["[HUALIEN] 乙"],
        "used": ["[HUALIEN] 甲"], "full": ["[HUALIEN] 甲"], "unused": ["[HUALIEN] 乙", "[HUALIEN] 丙"],
        "drive": None if kind == "flood_red" else {
            "max_hours": 0.3, "vehicles": 400, "local_people": 600, "uncounted_people": 0,
            "top": [{"label": "台9", "vehicles": 400, "capacity_vph": 1450, "hours": 0.3, "bridges": []}]},
    }


REPORT = {
    "meta": {"generated_at": "2026-11-14 10:00", "shelter_dataset_updated": "2025-12-16", "shelters": 3,
             "population_month": "民國 115 年 9 月", "villages": 2, "roads_loaded": "2026-11-02", "debris_zones": 1,
             "radius_km": 10, "evacuation_ratios": {"earthquake": 0.12, "flood": 0.22}},
    "townships": [town("甲鄉", "花蓮縣", 10000, 500), town("乙鄉", "臺東縣", 5000, 300, eq_shelters=0)],
    "scenarios": [scenario("甲鄉", "earthquake"), scenario("甲鄉", "flood"),
                  scenario("甲鄉", "flood_red", cut_off=[("甲鄉一村", 300), ("甲鄉二村", 900)])],
    "shelter_usage": {"[HUALIEN] 甲": {"used": 2, "full": 2, "unused": 0},
                      "[HUALIEN] 丙": {"used": 0, "full": 0, "unused": 2}},
}


def test_page_view_drops_the_per_scenario_shelter_lists():
    view = page_view(REPORT)
    assert {"used", "full", "unused", "unreachable", "drive"}.isdisjoint(view["scenarios"][0])
    # 道路瓶頸另外一張表：強震與淹水各一列
    assert [(r["kind"], r["slowest"]["label"], r["left_out"]) for r in view["bottlenecks"]] == [
        ("earthquake", "台9", 600), ("flood", "台9", 600)]
    assert (view["scenarios"][0]["full_count"], view["scenarios"][0]["unused_count"]) == (1, 2)
    assert view["cut_off"] == [{"township": "甲鄉", "village": "甲鄉二村", "population": 900},
                               {"township": "甲鄉", "village": "甲鄉一村", "population": 300}]
    assert view["busiest"] == [{"name": "甲", "full": 2}] and view["never_used"] == ["丙"]
    assert view["main_scenarios"] == 2
    assert any("**1 個鄉鎮沒有任何一間避難所列為震災適用**" in h for h in view["highlights"])
    assert view["checks"][0] == "沒有任何避難所列為震災適用：乙鄉。"


@pytest.fixture
def report_file(tmp_path, monkeypatch):
    path = tmp_path / "capacity_gap.json"
    monkeypatch.setattr(report_api, "REPORT_FILE", path)
    monkeypatch.setattr(report_api, "_cache", {"mtime": None, "view": None})
    return path


def test_report_page_is_served(client):  # noqa: F811
    res = client.get("/report")
    assert res.status_code == 200 and "容量缺口報告" in res.text


def test_report_api_says_how_to_generate_a_missing_report(client, report_file):  # noqa: F811
    res = client.get("/api/capacity_report")
    assert res.status_code == 404 and "tools/capacity_report.py" in res.json()["detail"]


def test_report_api_rereads_the_file_after_a_new_report(client, report_file):  # noqa: F811
    report_file.write_text(json.dumps(REPORT, ensure_ascii=False), encoding="utf-8")
    assert client.get("/api/capacity_report").json()["meta"]["generated_at"] == "2026-11-14 10:00"
    newer = {**REPORT, "meta": {**REPORT["meta"], "generated_at": "2026-11-15 09:00"}}
    report_file.write_text(json.dumps(newer, ensure_ascii=False), encoding="utf-8")
    stat = report_file.stat()
    os.utime(report_file, (stat.st_atime, stat.st_mtime + 5))
    assert client.get("/api/capacity_report").json()["meta"]["generated_at"] == "2026-11-15 09:00"
