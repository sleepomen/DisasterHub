"""
疏散分配：受影響人口往哪一間避難所走、各走多少人。

以前這一步在前端：瀏覽器用亂數撒出人群起點，逐點挑避難所，動畫跑完才把結果回寫後端。
同一組參數每次模擬分配都不一樣，AI 助手引用的數字也要等動畫結束、回寫成功之後才對得上。
現在由後端算出確定的分配並直接寫入資料庫，前端只負責把這份分配畫成動畫。

分配是運輸問題，用最小成本流解（services/simulation/min_cost_flow.py）：守住每間避難所的剩餘空位，
先安置最多的人，同樣人數下讓所有人的通行時間加總最短。結果可以重現。
以前用「全域最近優先」的貪婪法（所有配對依時間排序、依序塞滿）；2026-11-02 在 114 個情境比較
（evals/run_assignment_eval.py），兩者安置人數一樣，貪婪法的總時間平均只多 0.4%，最多多 6.4%。
最小成本流在每個指標都不比貪婪法差，解一次不到 0.05 秒，所以換掉。

距離用路網上的道路距離（services/routing.evacuation_road_distances）。個別算不出道路距離的配對
不能直接拿直線距離來比，直線一定比較短，會被排到前面；改用「直線 × DETOUR_FACTOR」估算
"""
from dataclasses import dataclass

from models.travel import DEFAULT_MODE, fallback_seconds
from services.simulation.min_cost_flow import min_cost_flow
from services.simulation.population import distance_km

# 哪些災害類型要避開哪一類潛勢區域。豪雨同時是淹水與土石流的觸發條件：淹水模擬時，
# 位在土石流潛勢溪流影響範圍內的避難所（實測：澳花國小就在高風險範圍「宜縣DF139」內）
# 不能當成疏散目的地。地震與火災目前沒有對應的潛勢圖資，不排除
EXCLUDED_HAZARDS = {"flood": "debris_flow"}
HAZARD_LABELS = {"debris_flow": "土石流潛勢溪流影響範圍"}
# 模擬的災害類型 → 消防署「適用災害類別」。沒列這一類的避難所不當疏散目的地；
# 類別欄空白（51 間）是來源沒填，不代表不適用，照常分配。火災沒有對應的類別
DESIGNATED_HAZARD = {"flood": "水災", "earthquake": "震災"}


def undesignated(shelters: list, sim_type: str) -> list[str]:
    """消防署資料明確列了適用災害、但不含這次災害類型的避難所名稱"""
    label = DESIGNATED_HAZARD.get(sim_type)
    if not label:
        return []
    return sorted(s.name for s in shelters if s.hazards and label not in s.hazards)


# 範圍外但離得很近的避難所只警告、不排除：影響範圍本身已經含堆積區，
# 排除會讓本來就不多的空位再少一間。200 公尺內實測只有南山國小（121 公尺），下一間是 482 公尺
HAZARD_WARN_M = 200

# 道路距離／直線距離的中位數。2026-10-24 實測（消防署 644 間、修正後的路網）：
# 29 個鄉鎮重心到 30 公里內的避難所共 4,287 組，中位數 1.163（p10 1.07、p90 1.55）。
# 只用在個別算不出道路距離的配對上
DETOUR_FACTOR = 1.16

# 範圍內沒有任何鄉鎮人口重心時（海域、無人山區），疏散人數由面積保底估算，起點就放在災害中心
FALLBACK_ORIGIN = "災害中心"


@dataclass
class Origin:
    name: str
    lat: float
    lon: float
    people: int


def apportion(total: int, weights: dict[str, int]) -> dict[str, int]:
    """
    依權重把 total 個人分給各鄉鎮，總和恰好等於 total（最大餘數法）。
    直接四捨五入的話，各鄉鎮加起來常會差一兩個人，跟「預估需疏散 N 人」對不上
    """
    weight_sum = sum(weights.values())
    if total <= 0 or weight_sum <= 0:
        return dict.fromkeys(weights, 0)
    exact = {name: total * w / weight_sum for name, w in weights.items()}
    share = {name: int(v) for name, v in exact.items()}
    left = total - sum(share.values())
    # 餘數相同時依名稱排序，結果才會固定
    for name in sorted(exact, key=lambda n: (-(exact[n] - share[n]), n))[:left]:
        share[name] += 1
    return share


def origins_from(population: dict, center_lat: float, center_lon: float) -> list[Origin]:
    """人口估算 → 疏散起點。需疏散的人數依各鄉鎮的受影響人口比例分攤"""
    evacuees = int(population.get("estimated_evacuees") or 0)
    townships = population.get("townships") or []
    if not townships:
        return [Origin(FALLBACK_ORIGIN, center_lat, center_lon, evacuees)] if evacuees > 0 else []
    shares = apportion(evacuees, {t["name"]: int(t["weight"]) for t in townships})
    return [Origin(t["name"], t["lat"], t["lon"], shares[t["name"]]) for t in townships if shares[t["name"]] > 0]


def split_hazard_hits(hits: dict[str, list[tuple[str, float]]]) -> tuple[dict[str, list[str]], dict[str, dict]]:
    """
    shelter_hazard_distances 的結果 → (位在區域內, 只是靠近)。
      inside  {名稱: [所在區域編號]}
      near    {名稱: {"refs": [附近區域編號], "distance_m": 最近距離（公尺，取整）}}
    """
    inside: dict[str, list[str]] = {}
    near: dict[str, dict] = {}
    for name, zones in hits.items():
        within = [ref for ref, d in zones if d == 0]
        if within:
            inside[name] = within
        elif zones:
            near[name] = {"refs": [ref for ref, _ in zones], "distance_m": round(min(d for _, d in zones))}
    return inside, near


def pair_costs(origins: list[Origin], shelters: list, road_m: dict, mode: str) -> dict[tuple[int, int], tuple[float, float, bool]]:
    """
    {(起點序號, 避難所序號): (每人的通行成本秒數, 直線公里, 是否依路線)}。沒有安全路線（None）的配對不在裡面；
    算不出路線的用直線 × DETOUR_FACTOR 換成時間，才能跟路線算出來的時間放在一起比
    """
    costs = {}
    for i, o in enumerate(origins):
        for j, s in enumerate(shelters):
            if (i, j) in road_m and road_m[(i, j)] is None:
                continue
            straight = round(distance_km(o.lat, o.lon, s.lat, s.lon), 2)
            routed = (i, j) in road_m
            cost = road_m[(i, j)] if routed else fallback_seconds(straight * 1000 * DETOUR_FACTOR, mode)
            costs[(i, j)] = (round(cost, 1), straight, routed)
    return costs


def plan_evacuation(
    origins: list[Origin], shelters: list, road_m: dict[tuple[int, int], float | None] | None = None,
    mode: str = DEFAULT_MODE,
) -> dict:
    """
    shelters 是 Shelter 物件（要有 name / lat / lon / remaining）。
    road_m 是 {(起點序號, 避難所序號): 路線成本（秒）}（mode 的通行時間，警戒或潛勢範圍再加權），
    沒有的配對用直線 × DETOUR_FACTOR 換算成時間；值是 None 代表沒有安全路線可到，這一組不分配。回傳：
      flows     每一筆「從哪個鄉鎮、到哪間避難所、多少人、直線距離」，依路線成本由低到高。
                人數由最小成本流決定：先安置最多的人，再讓所有人的通行時間加總最短
                routed 表示依路線分配；實際的 road_km / walk_min / drive_min 由 routing.attach_evacuation_paths 填上
      assigned  每間避難所分到的人數
      placed / unplaced  安置與安置不下的人數（總和等於需疏散人數）
      cut_off   哪些鄉鎮到任何一間避難所都沒有安全路線（人全部算在 unplaced 裡）
      unreachable  哪些避難所從任何一個鄉鎮都沒有安全路線可到（例如紅色警戒封了唯一的聯外道路）
      distance_basis  分配用的距離："road"（全部是道路距離）、"mixed"、"straight"
      mode      步行或開車（「近」是哪一種交通方式的時間）
    """
    road_m = road_m or {}
    costs = pair_costs(origins, shelters, road_m, mode)
    cut_off = [
        o.name for i, o in enumerate(origins)
        if shelters and o.people > 0 and all((i, j) in road_m and road_m[(i, j)] is None for j in range(len(shelters)))
    ]
    moved = min_cost_flow(
        [o.people for o in origins], [max(0, int(s.remaining)) for s in shelters],
        {pair: cost for pair, (cost, _, _) in costs.items()},
    )
    flows = []
    assigned: dict[str, int] = {}
    # 依通行時間由短到長列，同樣時間依名稱，結果可以重現
    for (i, j), people in sorted(moved.items(), key=lambda kv: (costs[kv[0]][0], origins[kv[0][0]].name, shelters[kv[0][1]].name)):
        origin, shelter = origins[i], shelters[j]
        _, straight, routed = costs[(i, j)]
        assigned[shelter.name] = assigned.get(shelter.name, 0) + people
        flows.append({
            "from": origin.name,
            "from_lat": origin.lat,
            "from_lon": origin.lon,
            "to": shelter.name,
            "to_lat": shelter.lat,
            "to_lon": shelter.lon,
            "people": people,
            "distance_km": straight,
            "routed": routed,
            "road_km": None,
        })
    placed = sum(assigned.values())
    routed = [f["routed"] for f in flows] or [bool(road_m)]
    return {
        "flows": flows,
        "assigned": assigned,
        "placed": placed,
        "unplaced": sum(o.people for o in origins) - placed,
        "cut_off": cut_off,
        "unreachable": [
            s.name for j, s in enumerate(shelters)
            if origins and all((i, j) in road_m and road_m[(i, j)] is None for i in range(len(origins)))
        ],
        "distance_basis": "road" if all(routed) else "mixed" if any(routed) else "straight",
        "mode": mode,
    }
