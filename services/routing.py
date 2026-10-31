"""
最近避難所：依道路距離排序，算不出來時退回直線距離。

東部山多、溪多，直線很近的避難所常常要繞一大圈才到得了：實測從太麻里鄉的人口重心出發，
北里活動中心直線 2.16 公里、走路網要 5.44 公里，香蘭國小直線 3.08 公里、路網只要 4.39 公里。

做法是先用直線距離（PostGIS KNN，有索引）挑出 CANDIDATES 間候選，再對這幾間算路網距離重新排序。
直線第 11 名以後的避難所不會被考慮，這是取捨：它在路網上反而比較近的機會很小，
而每多一個候選就多一組最短路徑要算。

路線會避開災害範圍（見 ShelterRepository._weights_sql）：土石流紅色警戒範圍內的道路封閉、
黃色警戒範圍內盡量繞開；淹水模擬另外繞開土石流潛勢範圍。排序依加權後的成本，
回報給使用者的距離是那條路線實際的公尺數。兩端都在路上卻找不到安全路線的，標成「到不了」，
不退回直線距離——直線會直接穿過封閉的範圍
"""
import logging

from repositories.shelter_repository import ORIGIN_SNAP_M
from services.metrics import metrics

logger = logging.getLogger(__name__)

CANDIDATES = 10

metrics.register(
    "routing.road", "routing.fallback", "routing.failed", "routing.unreachable",
    "routing.evacuation_road", "routing.evacuation_fallback", "routing.evacuation_failed", "routing.path_failed",
)


def nearest_by_road(repo, lat: float, lon: float, limit: int = 5) -> list:
    """
    回傳 NearbyShelter 清單：算得出路線的依路線成本排在前面，算不出道路距離的依直線距離接在後面，
    沒有安全路線可到的排最後（unreachable=True）。路網查詢失敗不影響回答，只是全部退回直線距離
    """
    candidates = repo.get_nearest_shelters(lat, lon, limit=max(limit, CANDIDATES))
    if not candidates:
        return []
    targets = [(n.shelter.lat, n.shelter.lon) for n in candidates]
    try:
        costs = repo.road_distances([(lat, lon)], targets)
    except Exception:
        metrics.incr("routing.failed")
        logger.exception("路網距離查詢失敗，改用直線距離")
        costs = {}
    for j, n in enumerate(candidates):
        n.unreachable = (0, j) in costs and costs[(0, j)] is None
        cost = costs.get((0, j))
        n.road_km = round(cost / 1000, 2) if cost is not None else None
    metrics.incr("routing.road" if costs else "routing.fallback")
    if any(n.unreachable for n in candidates):
        metrics.incr("routing.unreachable")

    def rank(n):
        if n.unreachable:
            return (2, n.distance_km)
        return (0, n.road_km) if n.road_km is not None else (1, n.distance_km)

    ranked = sorted(candidates, key=rank)[:limit]
    _replace_with_actual_distance(repo, lat, lon, ranked)
    return ranked


def _replace_with_actual_distance(repo, lat: float, lon: float, shelters: list) -> None:
    """
    排序用的是加權成本，有警戒時它比實際距離長。對最後要列出的幾間另外算實際路線，
    road_km 換成實際公里數，hazard_m 是路線仍經過警戒範圍的長度。查不到就沿用成本（沒有警戒時兩者相同）
    """
    routed = [(j, n) for j, n in enumerate(shelters) if n.road_km is not None]
    if not routed:
        return
    try:
        paths = repo.road_paths([(lat, lon)], [(n.shelter.lat, n.shelter.lon) for n in shelters], [(0, j) for j, _ in routed])
    except Exception:
        metrics.incr("routing.path_failed")
        logger.exception("最近避難所的實際路線查詢失敗，距離沿用路線成本")
        return
    for j, n in routed:
        path = paths.get((0, j))
        if path is not None:
            n.road_km = round(path.meters / 1000, 2)
            n.hazard_m = round(path.hazard_m)


def evacuation_road_distances(repo, origins: list, shelters: list, hazard: str | None = None) -> dict:
    """
    疏散起點（鄉鎮人口重心）× 目的地避難所的路線成本（公尺），給 plan_evacuation 用；None 是到不了。
    hazard 是這次模擬要繞開的潛勢範圍（淹水 → 土石流）。
    一次多對多查完：實測全部 29 個鄉鎮 × 644 間避難所 0.54 秒，所以不先篩候選。
    查詢失敗回傳空 dict，分配整個退回直線距離，模擬本身不能因此失敗
    """
    if not origins or not shelters:
        return {}
    try:
        costs = repo.road_distances(
            [(o.lat, o.lon) for o in origins], [(s.lat, s.lon) for s in shelters],
            origin_snap_m=ORIGIN_SNAP_M, hazard=hazard,
        )
    except Exception:
        metrics.incr("routing.evacuation_failed")
        logger.exception("疏散分配的路網距離查詢失敗，改用直線距離")
        return {}
    metrics.incr("routing.evacuation_road" if costs else "routing.evacuation_fallback")
    return costs


def attach_evacuation_paths(repo, origins: list, shelters: list, flows: list[dict], hazard: str | None = None) -> None:
    """
    每一筆有道路距離的分配附上實際路線：flow["path"]（encoded polyline，前端沿著路畫人群移動）、
    road_km 換成實際公里數（分配時用的是加權成本）、hazard_m 是仍經過災害範圍的長度。
    只算真的有人走的組合，不是全部鄉鎮 × 避難所。沒有道路距離或查詢失敗的 path 是 None，前端畫直線
    """
    origin_idx = {o.name: i for i, o in enumerate(origins)}
    shelter_idx = {s.name: j for j, s in enumerate(shelters)}
    pairs = sorted({(origin_idx[f["from"]], shelter_idx[f["to"]]) for f in flows if f["road_km"] is not None})
    paths = {}
    if pairs:
        try:
            paths = repo.road_paths(
                [(o.lat, o.lon) for o in origins], [(s.lat, s.lon) for s in shelters], pairs,
                origin_snap_m=ORIGIN_SNAP_M, hazard=hazard,
            )
        except Exception:
            metrics.incr("routing.path_failed")
            logger.exception("疏散路線查詢失敗，改畫直線")
    for f in flows:
        path = paths.get((origin_idx[f["from"]], shelter_idx[f["to"]]))
        f["path"] = path.polyline if path else None
        f["hazard_m"] = round(path.hazard_m) if path else 0
        if path:
            f["road_km"] = round(path.meters / 1000, 2)
