"""
疏散分配：受影響人口往哪一間避難所走、各走多少人。

以前這一步在前端：瀏覽器用亂數撒出人群起點，逐點挑避難所，動畫跑完才把結果回寫後端。
同一組參數每次模擬分配都不一樣，AI 助手引用的數字也要等動畫結束、回寫成功之後才對得上。
現在由後端算出確定的分配並直接寫入資料庫，前端只負責把這份分配畫成動畫。

演算法是「全域最近優先」的貪婪分配：把所有（鄉鎮, 避難所）配對依距離由近到遠排序，
依序把鄉鎮剩下的人塞進避難所剩下的空位。容量一定守得住、結果可重現、也容易對人解釋；
它不保證總移動距離最小（那是運輸問題，要用最小成本流），距離也還是直線距離而不是路網距離。
"""
from dataclasses import dataclass

from services.population_service import distance_km

# 哪些災害類型要避開哪一類潛勢區域。豪雨同時是淹水與土石流的觸發條件：淹水模擬時，
# 位在土石流潛勢溪流影響範圍內的避難所（實測：澳花國小就在高風險範圍「宜縣DF139」內）
# 不能當成疏散目的地。地震與火災目前沒有對應的潛勢圖資，不排除
EXCLUDED_HAZARDS = {"flood": "debris_flow"}
HAZARD_LABELS = {"debris_flow": "土石流潛勢溪流影響範圍"}
# 範圍外但離得很近的避難所只警告、不排除：影響範圍本身已經含堆積區，
# 排除會讓本來就不多的空位再少一間。200 公尺內實測只有南山國小（121 公尺），下一間是 482 公尺
HAZARD_WARN_M = 200

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


def plan_evacuation(origins: list[Origin], shelters: list) -> dict:
    """
    shelters 是 Shelter 物件（要有 name / lat / lon / remaining）。回傳：
      flows     每一筆「從哪個鄉鎮、到哪間避難所、多少人、直線距離」，依距離由近到遠
      assigned  每間避難所分到的人數
      placed / unplaced  安置與安置不下的人數（總和等於需疏散人數）
    """
    need = {o.name: o.people for o in origins}
    room = {s.name: max(0, int(s.remaining)) for s in shelters}
    pairs = sorted(
        (
            (round(distance_km(o.lat, o.lon, s.lat, s.lon), 2), o.name, s.name, o, s)
            for o in origins
            for s in shelters
        ),
        key=lambda p: p[:3],
    )
    flows = []
    assigned: dict[str, int] = {}
    for dist, _, _, origin, shelter in pairs:
        moving = min(need[origin.name], room[shelter.name])
        if moving <= 0:
            continue
        need[origin.name] -= moving
        room[shelter.name] -= moving
        assigned[shelter.name] = assigned.get(shelter.name, 0) + moving
        flows.append({
            "from": origin.name,
            "from_lat": origin.lat,
            "from_lon": origin.lon,
            "to": shelter.name,
            "to_lat": shelter.lat,
            "to_lon": shelter.lon,
            "people": moving,
            "distance_km": dist,
        })
    placed = sum(assigned.values())
    return {
        "flows": flows,
        "assigned": assigned,
        "placed": placed,
        "unplaced": sum(o.people for o in origins) - placed,
    }
