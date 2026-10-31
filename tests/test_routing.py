import gzip
import json
import pathlib
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from models.shelter import NearbyShelter, Shelter
from repositories.shelter_repository import FRACTION_EPS, RoadPath, ShelterRepository
from services.evacuation import Origin
from services.routing import attach_evacuation_paths, evacuation_road_distances, nearest_by_road

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from build_road_network import (  # noqa: E402
    RingIndex,
    drop_small_components,
    encode_polyline,
    in_any,
    split_at_junctions,
    stitch_rings,
)

NETWORK = ROOT / "data_reference" / "east_roads.json.gz"


def fake_repo_cursor(results):
    """依序回傳每次 execute 之後 fetchall 的結果"""
    cursor = MagicMock()
    cursor.fetchall.side_effect = list(results)

    @contextmanager
    def fake_cursor(self):
        yield cursor

    return cursor, fake_cursor


# ── 建置工具 ─────────────────────────────────────────

def test_polyline_matches_the_reference_encoding():
    # Google 文件的範例：(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)
    assert encode_polyline([(-120.2, 38.5), (-120.95, 40.7), (-126.453, 43.252)]) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"


def test_rings_with_a_gap_at_sea_are_still_closed():
    """宜蘭、臺東的縣界有一段在切片外的海上，接不回起點；第一版把整個本島丟掉了"""
    square_with_gap = [
        [(1, 0.0, 0.0), (2, 1.0, 0.0)],
        [(2, 1.0, 0.0), (3, 1.0, 1.0)],
        # (3) → (4) 這一段不在檔案裡
        [(4, 0.0, 1.0), (1, 0.0, 0.0)],
    ]
    rings = stitch_rings(square_with_gap)
    assert len(rings) == 1 and rings[0][0] == rings[0][-1]
    index = RingIndex(rings)
    assert index.contains(0.5, 0.5)
    assert not index.contains(1.5, 0.5)


def test_a_road_inside_two_overlapping_county_rings_is_kept():
    """臺東補缺口的直線切進花蓮南部：同一點落在兩縣的環裡，合成一個奇偶索引會被當成在外面"""
    hualien = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0), (0.0, 0.0)]
    taitung = [(1.0, -1.0), (3.0, -1.0), (3.0, 1.0), (1.0, 1.0), (1.0, -1.0)]
    road = [(1.4, 0.5), (1.5, 0.5), (1.6, 0.5)]
    assert in_any([RingIndex([hualien]), RingIndex([taitung])], road)
    # 第一版的寫法：兩縣丟進同一個索引，重疊處被奇偶規則判成在外面
    assert not RingIndex([hualien, taitung]).contains(1.5, 0.5)


def test_ways_are_split_at_shared_nodes_only():
    ways = [
        {"refs": [1, 2, 3], "coords": [(121.0, 24.0), (121.001, 24.0), (121.002, 24.0)], "highway": "primary"},
        {"refs": [2, 9], "coords": [(121.001, 24.0), (121.001, 24.001)], "highway": "residential"},
    ]
    edges = split_at_junctions(ways)
    assert sorted((e["source"], e["target"]) for e in edges) == [(1, 2), (2, 3), (2, 9)]
    assert all(e["length"] > 100 for e in edges)


def test_small_isolated_fragments_are_dropped():
    main = [{"source": i, "target": i + 1} for i in range(30)]
    island = [{"source": 100, "target": 101}]
    kept = drop_small_components(main + island, 20)
    assert len(kept) == 30


def test_bundled_network_covers_the_three_counties():
    network = json.loads(gzip.decompress(NETWORK.read_bytes()))
    assert network["source"]["license"].startswith("ODbL")
    assert network["counts"]["edges"] == len(network["edges"]) > 100000
    source, target, length, highway, polyline = network["edges"][0]
    assert isinstance(source, int) and length > 0 and highway and polyline


# ── 資料庫 ─────────────────────────────────────────

def test_road_distances_add_the_walk_to_and_from_the_road():
    cursor, fake_cursor = fake_repo_cursor([
        # 吸附：起點 0、兩個終點 1/2；終點 2 離路 1500 公尺，不吸附
        [(0, 10, 0.5, 20.0), (1, 11, 0.25, 30.0), (2, 12, 0.5, 1500.0)],
        [(-1, -2, 1000.0)],
    ])
    with patch.object(ShelterRepository, "_cursor", fake_cursor):
        result = ShelterRepository().road_distances([(24.0, 121.0)], [(24.01, 121.0), (24.5, 121.5)])
    assert result == {(0, 0): 1050.0}
    _, (edges_sql, points_sql, starts, ends) = cursor.execute.call_args.args
    assert starts == [-1] and ends == [-2]
    assert "(1, 10, 0.5)" in points_sql and "12" not in points_sql


def test_origins_may_snap_farther_than_shelters():
    """鄉鎮人口重心只是代表點，可能離路比較遠：起點的吸附上限另外設，終點（避難所）維持 1 公里"""
    _, fake_cursor = fake_repo_cursor([
        [(0, 10, 0.5, 3200.0), (1, 11, 0.5, 30.0), (2, 12, 0.5, 3200.0)],
        [(-1, -2, 1000.0)],
    ])
    with patch.object(ShelterRepository, "_cursor", fake_cursor):
        result = ShelterRepository().road_distances(
            [(23.33, 121.31)], [(23.34, 121.31), (23.5, 121.5)], origin_snap_m=5000,
        )
    assert result == {(0, 0): 4230.0}


def test_endpoint_fractions_are_nudged_inside_the_edge():
    """pgRouting 3.4 遇到 fraction 剛好 0 或 1 的點會整組回傳空結果（實測花蓮火車站一筆都算不出來）"""
    # 第二、三次查詢都沒有結果：子圖裡找不到，整個路網也找不到
    cursor, fake_cursor = fake_repo_cursor([[(0, 10, 1.0, 5.0), (1, 11, 0.0, 5.0)], [], []])
    with patch.object(ShelterRepository, "_cursor", fake_cursor):
        ShelterRepository().road_distances([(24.0, 121.0)], [(24.01, 121.0)])
    points_sql = cursor.execute.call_args.args[1][1]
    assert f"(1, 10, {1 - FRACTION_EPS})" in points_sql and f"(2, 11, {FRACTION_EPS})" in points_sql


def test_same_snap_point_is_zero_road_distance():
    _, fake_cursor = fake_repo_cursor([[(0, 10, 0.5, 3.0), (1, 10, 0.5, 4.0)], []])
    with patch.object(ShelterRepository, "_cursor", fake_cursor):
        assert ShelterRepository().road_distances([(24.0, 121.0)], [(24.0, 121.0)]) == {(0, 0): 7.0}


def test_road_paths_route_only_the_requested_pairs():
    """只算有人走的組合；吸附不到道路的終點直接略過，不送進 pgRouting"""
    cursor, fake_cursor = fake_repo_cursor([
        # 起點 0、1；終點 2、3（序號接在起點後面），終點 3 離路 1500 公尺
        [(0, 10, 0.5, 20.0), (1, 11, 0.0, 30.0), (2, 12, 0.25, 5.0), (3, 13, 0.5, 1500.0)],
        [(-1, -3, 1000.0, 0.0, "abc", 60.0), (-2, -3, 2000.0, 150.0, "def", 100.0)],
    ])
    with patch.object(ShelterRepository, "_cursor", fake_cursor):
        result = ShelterRepository().road_paths(
            [(24.0, 121.0), (24.1, 121.1)], [(24.01, 121.0), (24.5, 121.5)], [(0, 0), (1, 0), (0, 1)],
        )
    # 實際距離含走到路上、離開道路的兩小段；開車時間的這兩小段用步行速度（1 m/s）
    assert result == {(0, 0): RoadPath(1025.0, 0.0, "abc", 85.0), (1, 0): RoadPath(2035.0, 150.0, "def", 135.0)}
    assert result[(0, 0)].walk_s == 1025.0
    pids, fractions, _, points_sql, combinations_sql, _ = cursor.execute.call_args.args[1]
    assert pids == [1, 2, 3] and fractions == [0.5, FRACTION_EPS, 0.25]
    assert "(-1, -3), (-2, -3)" in combinations_sql and "-4" not in combinations_sql
    assert "13" not in points_sql


def test_road_paths_skip_the_database_without_pairs():
    with patch.object(ShelterRepository, "_cursor", side_effect=AssertionError("不該查詢")):
        assert ShelterRepository().road_paths([(24.0, 121.0)], [(24.01, 121.0)], []) == {}


def test_road_path_sql_follows_the_route_on_the_real_network(real_repo):
    """真的資料庫：路線從起點連到終點，長度加上走到路上的兩小段跟道路距離只差簡化誤差"""
    repo = real_repo
    try:
        with repo._cursor() as cursor:
            cursor.execute("SELECT count(*) FROM roads")
            if cursor.fetchone()[0] == 0:
                pytest.skip("路網還沒匯入")
    except Exception:
        pytest.skip("沒有資料庫")
    origins, targets = [(23.9930, 121.6015)], [(23.98, 121.55)]
    meters = repo.road_distances(origins, targets)[(0, 0)]
    path = repo.road_paths(origins, targets, [(0, 0)])[(0, 0)]
    polyline = path.polyline
    assert abs(path.meters - meters) / meters < 0.01
    with repo._cursor() as cursor:
        cursor.execute(
            "SELECT ST_Length(g::geography), ST_Distance(ST_StartPoint(g)::geography, ST_SetSRID(ST_Point(%s, %s), 4326)::geography),"
            " ST_Distance(ST_EndPoint(g)::geography, ST_SetSRID(ST_Point(%s, %s), 4326)::geography)"
            " FROM ST_LineFromEncodedPolyline(%s, 5) AS g",
            (origins[0][1], origins[0][0], targets[0][1], targets[0][0], polyline),
        )
        length, head_gap, tail_gap = cursor.fetchone()
    assert head_gap < 1000 and tail_gap < 1000
    assert abs(length + head_gap + tail_gap - meters) / meters < 0.02


def test_unchanged_network_is_not_reimported():
    cursor = MagicMock()
    cursor.fetchone.return_value = ("abc",)

    @contextmanager
    def fake_cursor(self):
        yield cursor

    with patch.object(ShelterRepository, "_cursor", fake_cursor):
        assert ShelterRepository().replace_road_network([[1, 2, 10.0, "primary", "??"]], "abc") is None
    executed = " ".join(c.args[0] for c in cursor.execute.call_args_list)
    assert "TRUNCATE" not in executed


def test_road_sync_failure_never_breaks_shelter_sync():
    from services.sync_service import DataSyncService

    svc = DataSyncService.__new__(DataSyncService)
    svc.repository = MagicMock()
    svc.repository.replace_road_network.side_effect = RuntimeError("no pgrouting")
    assert svc.sync_road_network() is None
    svc.repository.replace_road_network.side_effect = None
    svc.repository.replace_road_network.return_value = 109059
    assert svc.sync_road_network() == 109059


# ── 排序 ─────────────────────────────────────────

def test_nearest_by_road_reorders_and_keeps_unroutable_last():
    repo = MagicMock()
    repo.get_nearest_shelters.return_value = [
        NearbyShelter(Shelter("近但繞山", 100, 24.33, 121.73), distance_km=7.9),
        NearbyShelter(Shelter("遠但有路", 100, 24.46, 121.80), distance_km=8.8),
        NearbyShelter(Shelter("到不了", 100, 24.50, 121.50), distance_km=2.0),
    ]
    repo.road_distances.return_value = {(0, 0): 25600.0, (0, 1): 15550.0}
    repo.road_paths.return_value = {(0, 0): RoadPath(15550.0, 0.0, "a", 1100.0), (0, 1): RoadPath(25600.0, 0.0, "b", 1900.0)}
    ranked = nearest_by_road(repo, 24.4, 121.75, limit=3)
    assert [n.shelter.name for n in ranked] == ["遠但有路", "近但繞山", "到不了"]
    assert [n.road_km for n in ranked] == [15.55, 25.6, None]
    assert [(n.walk_min, n.drive_min) for n in ranked] == [(259, 18), (427, 32), (None, None)]
    assert repo.road_distances.call_args.kwargs == {"mode": "walk"}
    repo.get_nearest_shelters.assert_called_once_with(24.4, 121.75, limit=10)


def test_nearest_ranks_by_route_cost_but_reports_actual_distance():
    """
    有警戒時排序用加權成本（繞開警戒範圍），列出的距離換成實際路線的公里數；
    被紅色警戒封路擋住的排最後並標成到不了，不退回直線距離
    """
    repo = MagicMock()
    repo.get_nearest_shelters.return_value = [
        NearbyShelter(Shelter("封路後到不了", 100, 24.33, 121.73), distance_km=1.0),
        NearbyShelter(Shelter("要穿過黃色警戒", 100, 24.46, 121.80), distance_km=2.0),
        NearbyShelter(Shelter("安全", 100, 24.50, 121.50), distance_km=3.0),
    ]
    # 第二間實際 3 公里，但有 1 公里在黃色警戒範圍內，成本被加權到 7 公里
    repo.road_distances.return_value = {(0, 0): None, (0, 1): 7000.0, (0, 2): 4000.0}
    # 實際路線依排序後的順序查：安全、要穿過黃色警戒
    repo.road_paths.return_value = {(0, 0): RoadPath(4000.0, 0.0, "a"), (0, 1): RoadPath(3000.0, 1000.0, "b")}
    ranked = nearest_by_road(repo, 24.4, 121.75, limit=3)
    assert [n.shelter.name for n in ranked] == ["安全", "要穿過黃色警戒", "封路後到不了"]
    assert [(n.road_km, n.hazard_m, n.unreachable) for n in ranked] == [
        (4.0, 0, False), (3.0, 1000, False), (None, 0, True),
    ]
    # 只對列出來、算得到路線的那幾間查實際路線
    assert repo.road_paths.call_args.args[2] == [(0, 0), (0, 1)]


def test_evacuation_distances_use_the_wider_origin_snap():
    repo = MagicMock()
    repo.road_distances.return_value = {(0, 0): 4200.0}
    origins = [Origin("玉里鎮", 23.33, 121.31, 100)]
    shelters = [Shelter("玉里國小", 300, 23.34, 121.31)]
    assert evacuation_road_distances(repo, origins, shelters) == {(0, 0): 4200.0}
    assert repo.road_distances.call_args.kwargs["origin_snap_m"] == 5000
    assert evacuation_road_distances(repo, [], shelters) == {}


def test_evacuation_distances_failure_returns_empty():
    repo = MagicMock()
    repo.road_distances.side_effect = RuntimeError("no pgrouting")
    assert evacuation_road_distances(repo, [Origin("甲", 24.0, 121.6, 1)], [Shelter("乙", 1, 24.1, 121.6)]) == {}


def test_evacuation_paths_only_for_flows_with_road_distance():
    repo = MagicMock()
    repo.road_paths.return_value = {(0, 1): RoadPath(1800.0, 250.0, "abc", 300.0)}
    origins = [Origin("玉里鎮", 23.33, 121.31, 100)]
    shelters = [Shelter("甲", 300, 23.34, 121.31), Shelter("乙", 300, 23.35, 121.32)]
    flows = [
        {"from": "玉里鎮", "to": "乙", "routed": True, "road_km": None},
        {"from": "玉里鎮", "to": "甲", "routed": False, "road_km": None},
    ]
    attach_evacuation_paths(repo, origins, shelters, flows, "debris_flow", "drive")
    assert [f["path"] for f in flows] == ["abc", None]
    # 實際路線的距離與時間（分配時用的是加權後的時間）
    assert [(f["road_km"], f["hazard_m"], f["walk_min"], f["drive_min"]) for f in flows] == [
        (1.8, 250, 30, 5), (None, 0, None, None),
    ]
    assert repo.road_paths.call_args.args[2] == [(0, 1)]
    assert repo.road_paths.call_args.kwargs == {"origin_snap_m": 5000, "hazard": "debris_flow", "mode": "drive"}


def test_evacuation_path_failure_draws_straight_lines():
    repo = MagicMock()
    repo.road_paths.side_effect = RuntimeError("no pgrouting")
    flows = [{"from": "甲", "to": "乙", "routed": True, "road_km": None}]
    attach_evacuation_paths(repo, [Origin("甲", 24.0, 121.6, 1)], [Shelter("乙", 1, 24.1, 121.6)], flows)
    assert flows[0]["path"] is None and flows[0]["road_km"] is None
