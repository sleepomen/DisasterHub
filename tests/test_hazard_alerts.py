"""即時土石流警戒：解析來源資料、封路與繞行的成本、到不了的組合"""
from unittest.mock import MagicMock, patch

import pytest

from models.shelter import Shelter
from repositories.shelter_repository import CAUTION_FACTOR, ShelterRepository
from services.chat_service import route_lines
from services.evacuation import Origin, plan_evacuation
from services.hazard_alerts import AlertFormatError, parse_debris_alerts, refresh_alerts
from tests.test_routing import fake_repo_cursor

# 來源沒有警戒時回傳的佔位資料（2026-10-30 實際抓到的內容）
NO_ALERT = [{
    "AlertType": "-", "DebrisNo": "-", "LandslideID": "-", "LandslideName": "目前無警戒", "County": "-",
    "Town": "-", "Vill": "-", "AlertLevel": "-", "LastUpdateDate": "2026-10-30T00:00:00",
    "CountyCode": "-", "AreaCode": "-", "LandslideNo": "-",
}]


def alert_row(debris_no, level, alert_type="D"):
    return {
        "AlertType": alert_type, "DebrisNo": debris_no, "LandslideID": "-", "LandslideName": "-",
        "County": "宜蘭縣", "Town": "南澳鄉", "Vill": "東岳村", "AlertLevel": level,
        "LastUpdateDate": "2026-08-24T12:00:00",
    }


def test_no_alert_placeholder_means_no_alerts():
    assert parse_debris_alerts(NO_ALERT) == []


def test_debris_alerts_are_parsed_and_landslide_alerts_skipped():
    alerts = parse_debris_alerts([
        alert_row("宜縣DF115", "r"),
        alert_row("臺東DF001", "y"),
        alert_row("-", "r", alert_type="L"),
    ])
    assert [(a["ref_no"], a["level"]) for a in alerts] == [("宜縣DF115", "red"), ("台東DF001", "yellow")]
    assert alerts[0]["village"] == "東岳村" and alerts[0]["issued_at"] == "2026-08-24T12:00:00+08:00"


@pytest.mark.parametrize("payload", [{"message": "error"}, [{"foo": 1}], "oops"])
def test_unexpected_payload_is_an_error_not_an_empty_list(payload):
    """格式不對時丟例外：當成「沒有警戒」會把紅色警戒封閉的道路打開"""
    with pytest.raises(AlertFormatError):
        parse_debris_alerts(payload)


def test_refresh_replaces_all_alerts_of_the_source():
    repo = MagicMock()
    repo.replace_alerts.return_value = 1
    with patch("services.hazard_alerts.fetch_debris_alerts", return_value=[{"ref_no": "宜縣DF115", "level": "red"}]):
        assert refresh_alerts(repo) == 1
    source, hazard_type, alerts, ttl = repo.replace_alerts.call_args.args
    assert hazard_type == "debris_flow" and alerts[0]["ref_no"] == "宜縣DF115" and ttl > 0


def test_failed_fetch_keeps_the_previous_alerts():
    repo = MagicMock()
    with patch("services.hazard_alerts.fetch_debris_alerts", side_effect=OSError("timeout")):
        with pytest.raises(OSError):
            refresh_alerts(repo)
    repo.replace_alerts.assert_not_called()


# ── 路線成本 ─────────────────────────────────────────

def test_weights_close_red_alerts_and_weight_the_part_inside():
    sql = ShelterRepository._weights_sql(None)
    assert "road_closures" in sql and "road_hazards" not in sql
    assert f"WHEN 'caution' THEN {CAUTION_FACTOR}" in sql
    # 加權只套在落在範圍內的那一段
    assert "1 + (factor - 1) * inside_ratio" in sql
    with_hazard = ShelterRepository._weights_sql("debris_flow")
    assert "FROM road_hazards WHERE hazard_type = 'debris_flow'" in with_hazard and "WHEN '高' THEN 3.0" in with_hazard


def test_unknown_hazard_is_never_put_into_sql():
    with pytest.raises(KeyError):
        ShelterRepository._weights_sql("x'; DROP TABLE roads; --")


def test_the_origin_edge_is_never_closed():
    """人就在紅色警戒範圍裡、正要往外撤：腳下那條路就算在範圍內也要能走"""
    snaps = {0: (101, 0.5, 5.0), 1: (202, 0.5, 5.0)}
    edges_sql, _ = ShelterRepository._routing_sql([(24.0, 121.0), (24.1, 121.1)], snaps, [0, 1], [0], None)
    assert "w.closed AND r.id NOT IN (101)" in edges_sql and "ST_MakeEnvelope" in edges_sql
    unbounded, _ = ShelterRepository._routing_sql([(24.0, 121.0), (24.1, 121.1)], snaps, [0, 1], [0], None, bounded=False)
    assert "ST_MakeEnvelope" not in unbounded


def test_unroutable_pairs_retry_on_the_whole_network_then_are_unreachable():
    cursor, fake_cursor = fake_repo_cursor([
        [(0, 10, 0.5, 20.0), (1, 11, 0.5, 30.0), (2, 12, 0.5, 40.0)],
        [(-1, -2, 1000.0)],  # 子圖裡只找得到第一間
        [],                   # 整個路網也找不到第二間
    ])
    with patch.object(ShelterRepository, "_cursor", fake_cursor):
        result = ShelterRepository().road_distances([(24.0, 121.0)], [(24.01, 121.0), (24.5, 121.5)])
    assert result == {(0, 0): 1050.0, (0, 1): None}
    edges_sql, _, starts, ends = cursor.execute.call_args.args[1]
    assert "ST_MakeEnvelope" not in edges_sql and starts == [-1] and ends == [-3]


# ── 疏散分配 ─────────────────────────────────────────

def test_unreachable_pairs_are_not_assigned_and_cut_off_areas_reported():
    origins = [Origin("東澳", 24.52, 121.83, 100), Origin("南澳", 24.46, 121.80, 50)]
    shelters = [Shelter("東澳國小", 500, 24.53, 121.84), Shelter("南澳國小", 30, 24.46, 121.80)]
    # 東澳到兩間都被封路擋住；南澳只能去南澳國小
    road_m = {(0, 0): None, (0, 1): None, (1, 0): None, (1, 1): 800.0}
    plan = plan_evacuation(origins, shelters, road_m)
    assert plan["cut_off"] == ["東澳"]
    # 東澳國小從哪裡都到不了，南澳國小南澳到得了
    assert plan["unreachable"] == ["東澳國小"]
    assert plan["assigned"] == {"南澳國小": 30}
    assert plan["placed"] == 30 and plan["unplaced"] == 120
    assert all(f["to"] != "東澳國小" for f in plan["flows"])


def test_route_notes_mention_alerts_detours_and_cut_off_areas():
    plan = {
        "route_hazard": "debris_flow",
        "alerts": [{"ref_no": "宜縣DF115", "level": "red"}, {"ref_no": "宜縣DF096", "level": "yellow"}],
        "flows": [{"hazard_m": 313}, {"hazard_m": 0}],
        "cut_off": ["南澳鄉"],
        "unreachable": ["[YILAN] 東澳國小"],
    }
    lines = route_lines(plan)
    assert lines[0].startswith("目前有 1 條潛勢溪流發布土石流紅色警戒")
    assert "疏散路線已盡量繞開土石流潛勢溪流影響範圍" in lines[1]
    assert "1 條路線沒有更安全的道路" in lines[2] and "0.31 公里" in lines[2]
    assert lines[3] == "以下避難所的聯外道路被封閉，沒有安全路線可到，本次未列為疏散目的地：東澳國小。"
    assert lines[4] == "以下地區沒有安全道路可到任何避難所，人數計入無法安置：南澳鄉。"
    assert "Nan'ao Township" in route_lines(plan, "en")[-1]
    assert route_lines({}) == []


# ── 真的資料庫 ─────────────────────────────────────────

def test_red_alert_closes_the_suhua_road_on_the_real_network(real_repo):
    """
    南澳往東澳只有蘇花公路一條路，經過 宜縣DF096/097/115/116 的影響範圍（2026-10-30 實測）。
    紅色警戒時東澳到不了；解除後恢復
    """
    repo = real_repo
    try:
        with repo._cursor() as cursor:
            cursor.execute("SELECT count(*) FROM road_hazards")
            if cursor.fetchone()[0] == 0:
                pytest.skip("道路與潛勢範圍的對應還沒建立")
    except Exception:
        pytest.skip("沒有資料庫")
    origin, targets = [(24.46, 121.80)], [(24.53, 121.85), (24.47, 121.79)]
    refs = ["宜縣DF096", "宜縣DF097", "宜縣DF115", "宜縣DF116"]
    try:
        before = repo.road_distances(origin, targets)
        repo.replace_alerts("test", "debris_flow", [{"ref_no": r, "level": "red"} for r in refs], 10)
        closed = repo.road_distances(origin, targets)
        assert {a["ref_no"] for a in repo.active_alerts() if a["source"] == "test"} == set(refs)
    finally:
        repo.replace_alerts("test", "debris_flow", [], 10)
    assert before[(0, 0)] is not None and closed[(0, 0)] is None
    # 南澳鎮上那間不經過警戒範圍，不受影響
    assert closed[(0, 1)] == pytest.approx(before[(0, 1)])
