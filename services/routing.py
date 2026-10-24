"""
最近避難所：依道路距離排序，算不出來時退回直線距離。

東部山多、溪多，直線很近的避難所常常要繞一大圈才到得了：實測從南澳往南，
澳花國小直線 7.9 公里、走路網要 25.6 公里，南澳鄉綜合體育場直線 8.8 公里、路網只要 15.6 公里。

做法是先用直線距離（PostGIS KNN，有索引）挑出 CANDIDATES 間候選，再對這幾間算路網距離重新排序。
直線第 11 名以後的避難所不會被考慮，這是取捨：它在路網上反而比較近的機會很小，
而每多一個候選就多一組最短路徑要算
"""
import logging

from repositories.shelter_repository import ORIGIN_SNAP_M
from services.metrics import metrics

logger = logging.getLogger(__name__)

CANDIDATES = 10

metrics.register(
    "routing.road", "routing.fallback", "routing.failed",
    "routing.evacuation_road", "routing.evacuation_fallback", "routing.evacuation_failed",
)


def nearest_by_road(repo, lat: float, lon: float, limit: int = 5) -> list:
    """
    回傳 NearbyShelter 清單：算得出道路距離的依道路距離排在前面，其餘依直線距離接在後面。
    路網查詢失敗不影響回答，只是全部退回直線距離
    """
    candidates = repo.get_nearest_shelters(lat, lon, limit=max(limit, CANDIDATES))
    if not candidates:
        return []
    try:
        meters = repo.road_distances([(lat, lon)], [(n.shelter.lat, n.shelter.lon) for n in candidates])
    except Exception:
        metrics.incr("routing.failed")
        logger.exception("路網距離查詢失敗，改用直線距離")
        meters = {}
    for j, n in enumerate(candidates):
        n.road_km = round(meters[(0, j)] / 1000, 2) if (0, j) in meters else None
    metrics.incr("routing.road" if meters else "routing.fallback")
    ranked = sorted(candidates, key=lambda n: (n.road_km is None, n.road_km if n.road_km is not None else n.distance_km))
    return ranked[:limit]


def evacuation_road_distances(repo, origins: list, shelters: list) -> dict[tuple[int, int], float]:
    """
    疏散起點（鄉鎮人口重心）× 目的地避難所的道路距離（公尺），給 plan_evacuation 用。
    一次多對多查完：實測全部 29 個鄉鎮 × 50 間避難所 0.68 秒，所以不先篩候選。
    查詢失敗回傳空 dict，分配整個退回直線距離，模擬本身不能因此失敗
    """
    if not origins or not shelters:
        return {}
    try:
        meters = repo.road_distances(
            [(o.lat, o.lon) for o in origins], [(s.lat, s.lon) for s in shelters], origin_snap_m=ORIGIN_SNAP_M,
        )
    except Exception:
        metrics.incr("routing.evacuation_failed")
        logger.exception("疏散分配的路網距離查詢失敗，改用直線距離")
        return {}
    metrics.incr("routing.evacuation_road" if meters else "routing.evacuation_fallback")
    return meters
