"""services/simulation/bottlenecks.py：開車疏散的道路瓶頸與清空時間"""
from repositories.shelter_repository import RoadPath
from services.simulation.bottlenecks import (
    END_EXCLUDED_M,
    capacity_vph,
    counted_edges,
    find_bottlenecks,
    lanes_per_direction,
    road_label,
)


def edge(highway="primary", lanes=0, oneway=0, name="", ref="9", bridge=False, length_m=1000.0, lat=23.3, lon=121.3):
    return {"highway": highway, "lanes": lanes, "oneway": oneway, "name": name, "ref": ref, "bridge": bridge,
            "length_m": length_m, "lat": lat, "lon": lon}


def route(*edges):
    """(邊, 方向, 公尺) 前後各墊 END_EXCLUDED_M 公尺的巷子（編號 0），中間才算"""
    pad = (0, True, float(END_EXCLUDED_M))
    return RoadPath(0.0, 0.0, "", 0.0, (pad, *edges, pad))


def test_lanes_per_direction_from_the_osm_total():
    assert lanes_per_direction("primary", 4, 0) == 2
    assert lanes_per_direction("primary", 2, 0) == 1
    # 沒標：雙向道路一個方向一車道，國道單行道一個方向兩車道
    assert lanes_per_direction("primary", 0, 0) == 1
    assert lanes_per_direction("motorway", 0, 1) == 2
    assert lanes_per_direction("primary", 3, 1) == 3


def test_capacity_follows_the_taiwan_highway_capacity_manual():
    # 雙車道公路：雙向 2,900 → 每個方向 1,450
    assert capacity_vph("primary", 2, 0) == 1450
    # 國道外側車道 2,000 × 2
    assert capacity_vph("motorway", 0, 1) == 4000
    # 多車道依自由速率：省道 50 km/h → 1,700；縣道 40 km/h 低於表 11.6 的範圍，也用 1,700
    assert capacity_vph("primary", 4, 0) == 3400
    assert capacity_vph("secondary", 4, 0) == 3400
    assert capacity_vph("trunk", 4, 0) == 3600
    # 巷弄：手冊沒有，假設是雙車道公路的一半
    assert capacity_vph("residential", 0, 0) == 725


def test_road_labels():
    assert road_label(edge(ref="9", name="玉興橋")) == "台9 玉興橋"
    assert road_label(edge(ref="9;11")) == "台9/台11"
    assert road_label(edge(highway="tertiary", ref="花71")) == "花71"
    # 縣道在 OSM 也只寫數字；省道、縣道用編號分，不看道路等級
    assert road_label(edge(highway="secondary", ref="192甲;宜5", name="大坡路二段")) == "縣192甲/宜5 大坡路二段"
    assert road_label(edge(highway="tertiary", ref="20")) == "台20"
    assert road_label(edge(highway="primary", ref="193")) == "縣193"
    assert road_label(edge(highway="motorway", ref="5", name="蔣渭水高速公路")) == "國道5 蔣渭水高速公路"
    assert road_label(edge(highway="residential", ref="", name="中正路")) == "中正路"
    assert road_label(edge(highway="residential", ref="")) == "未命名巷弄"


def test_route_ends_are_not_counted():
    """整個村里從同一點出發：起點、終點附近那一小段不算，否則巷口永遠是最大的瓶頸"""
    edges = ((1, True, 600.0), (2, True, 600.0), (3, False, 2000.0), (4, True, 600.0), (5, True, 600.0))
    # 中點位置：300、900、2200、3500、4100；總長 4400
    assert counted_edges(edges, end_excluded_m=1000) == [(3, False)]
    assert counted_edges(edges) == [(2, True), (3, False), (4, True)]
    assert counted_edges(edges, end_excluded_m=0) == [(1, True), (2, True), (3, False), (4, True), (5, True)]


def test_shared_roads_add_up_and_directions_are_separate():
    paths = {
        ("甲村", "A國小"): route((10, True, 1000.0)),
        ("乙村", "A國小"): route((10, True, 1000.0)),
        # 反方向走同一條路，不跟上面兩組搶同一個車道
        ("丙村", "B國中"): route((10, False, 1000.0)),
    }
    flows = [{"from": "甲村", "to": "A國小", "people": 2000}, {"from": "乙村", "to": "A國小", "people": 1625},
             {"from": "丙村", "to": "B國中", "people": 500}]
    result = find_bottlenecks(flows, paths, {0: edge(highway="residential", ref=""), 10: edge(name="玉興橋", bridge=True)})
    top = result["top"][0]
    # 3,625 人 ÷ 2.5 = 1,450 輛，雙車道公路一個方向一小時 1,450 輛
    assert (top["label"], top["bridges"], top["vehicles"], top["capacity_vph"], top["hours"]) == ("台9 玉興橋", ["玉興橋"], 1450, 1450, 1.0)
    # 同一條路反方向的那一段不再列：每條路只列最慢的一段
    assert len(result["top"]) == 1
    assert result["max_hours"] == 1.0 and result["vehicles"] == 1650 and result["uncounted_people"] == 0


def test_both_directions_with_the_same_traffic_stay_apart():
    paths = {("甲村", "A國小"): route((10, True, 1000.0)), ("乙村", "B國中"): route((10, False, 1000.0))}
    flows = [{"from": "甲村", "to": "A國小", "people": 500}, {"from": "乙村", "to": "B國中", "people": 500}]
    [g] = find_bottlenecks(flows, paths, {10: edge()})["top"]
    # 不是兩個方向合成一段 2 公里
    assert (g["vehicles"], g["length_m"]) == (200, 1000)


def test_consecutive_edges_with_the_same_traffic_are_one_bottleneck():
    paths = {("甲村", "A國小"): route((10, True, 1000.0), (11, True, 500.0), (12, True, 800.0))}
    info = {10: edge(length_m=1000.0, lat=1.0), 11: edge(length_m=500.0, lat=2.0), 12: edge(ref="11", length_m=800.0)}
    result = find_bottlenecks([{"from": "甲村", "to": "A國小", "people": 500}], paths, info)
    assert [(g["label"], g["length_m"]) for g in result["top"]] == [("台11", 800), ("台9", 1500)]
    # 標在最長那條邊的中點
    assert result["top"][1]["lat"] == 1.0


def test_one_road_with_the_same_traffic_is_one_bottleneck_even_when_its_name_changes():
    """台9 從中正路一段換到二段、中間過一座橋：同一股車流是同一個瓶頸，不是三筆"""
    paths = {("甲村", "A國小"): route((10, True, 800.0), (11, True, 200.0), (12, True, 1200.0))}
    info = {10: edge(name="中正路一段", length_m=800.0), 11: edge(name="礁溪橋", bridge=True, length_m=200.0),
            12: edge(name="中正路二段", length_m=1200.0, lat=24.8, bridge=True)}
    result = find_bottlenecks([{"from": "甲村", "to": "A國小", "people": 1000}], paths, info)
    [g] = result["top"]
    # 中正路二段在 OSM 上也標成橋，但名字是路名，不列進橋名
    assert (g["label"], g["bridge"], g["bridges"], g["length_m"], g["lat"]) == ("台9 中正路二段", True, ["礁溪橋"], 2200, 24.8)


def test_each_road_is_listed_once_at_its_slowest_stretch():
    paths = {("甲村", "A國小"): route((10, True, 1000.0), (11, True, 1000.0)),
             ("乙村", "A國小"): route((10, True, 1000.0))}
    flows = [{"from": "甲村", "to": "A國小", "people": 500}, {"from": "乙村", "to": "A國小", "people": 400}]
    result = find_bottlenecks(flows, paths, {10: edge(name="中正路一段"), 11: edge(name="中正路二段")})
    assert [(g["label"], g["people"]) for g in result["top"]] == [("台9 中正路一段", 900)]


def test_routes_too_short_to_count_are_reported_as_local():
    """路線整條都在頭尾 500 公尺內：市區避難所很密，這種人不少，要說出來，不能默默消失"""
    paths = {("甲村", "A國小"): route((10, True, 1000.0)),
             ("乙村", "B國中"): RoadPath(0.0, 0.0, "", 0.0, ((10, True, 900.0),))}
    flows = [{"from": "甲村", "to": "A國小", "people": 100}, {"from": "乙村", "to": "B國中", "people": 300}]
    result = find_bottlenecks(flows, paths, {10: edge()})
    assert result["local_people"] == 300 and result["vehicles"] == 40 and result["top"][0]["people"] == 100


def test_people_without_a_road_route_are_reported_not_hidden():
    paths = {("甲村", "A國小"): route((10, True, 1000.0)), ("乙村", "B國中"): RoadPath(0.0, 0.0, "", 0.0)}
    flows = [{"from": "甲村", "to": "A國小", "people": 100}, {"from": "乙村", "to": "B國中", "people": 40},
             {"from": "丙村", "to": "B國中", "people": 7}]
    result = find_bottlenecks(flows, paths, {10: edge()})
    assert result["uncounted_people"] == 47 and result["vehicles"] == 40


def test_nothing_to_count():
    result = find_bottlenecks([], {}, {})
    assert result["top"] == [] and result["max_hours"] == 0.0 and result["vehicles"] == 0
