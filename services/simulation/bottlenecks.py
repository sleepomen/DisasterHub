"""
開車疏散的道路瓶頸與清空時間：分配不變，只是把每一組有人走的路線拆成一段一段的路，
加總每段路（分方向）上要通過多少輛車，除以那段路每小時的容量。

這是下限，不是預測：
- 假設所有人同時出發，也不模擬車隊回堵到上游；實際會更慢。
- 只算路段，不算號誌路口。市區的瓶頸多半在路口，這裡會低估。
- 車流全部當作小客車，沒有算機車與大車。
步行不算：最大的村里（吉安鄉慶豐村，7,835 人）全部走同一條路，有效寬 2 公尺的步道一小時也走得完
（2011 年版臺灣公路容量手冊表 19.5，壅塞時每公尺每分鐘 72～80 人）。

容量的出處是交通部運輸研究所《2011 年臺灣公路容量手冊》，單位是小客車／小時，每個方向：
- 國道：外側車道 2,000（第 4 章；內側車道可達 2,300，取保守的值）
- 一個方向兩車道以上的一般道路：依自由速率 1,700（50 公里／小時）到 2,100（90 公里／小時），表 11.6
- 一個方向一車道（雙車道公路）：雙向合計 2,900，每個方向 1,450（第 12 章；手冊自己註明有待現場資料證實）
- 巷弄、產業道路（unclassified 以下）：手冊沒有，假設是雙車道公路的一半。這是這裡最沒根據的數字
車道數只有約 5% 的道路有標，沒標的依道路等級給預設值（見 lanes_per_direction）。
"""
from models.travel import DEFAULT_DRIVE_KMH, DRIVE_KMH

# 一輛車平均載幾個人（假設；疏散時全家一起走，比平日通勤的 1.3 人高）
PEOPLE_PER_VEHICLE = 2.5
# 路線頭尾這麼長不算：一個村里的人全部從同一個點出發、到同一個門口，
# 吸附到的那一小段巷子會變成假的瓶頸。實際上人是從整個村里的各條路出發的。
# 500 公尺大約是市區一個村里的半徑。2026-11-16 用 80 個情境（41 鄉鎮 × 淹水、強震，開車）比較：
#   1,000 公尺  80% 的人路線短到整條不算，最慢的是宜蘭市強震台9中正路一段 0.66 小時
#     500 公尺  60%，同一段 0.69 小時
#     250 公尺  42%，同一段 0.73 小時，但宜蘭市的公正路這類巷弄開始排進前面，正是要排除的假瓶頸
END_EXCLUDED_M = 500
TOP_N = 10

MOTORWAY_LANE_VPH = 2000
TWO_LANE_VPH = 1450
MINOR_ROAD_VPH = 725
MULTILANE_MIN_VPH, MULTILANE_MAX_VPH = 1700, 2100
MINOR_HIGHWAYS = {"unclassified", "residential", "living_street", "service", "track"}
# 沒標車道數時，單行道（多半是分隔島兩側各畫一條的省道、國道）一個方向幾車道
DEFAULT_ONEWAY_LANES = {"motorway": 2, "trunk": 2}

HIGHWAY_TEXT = {
    "motorway": "國道", "motorway_link": "國道匝道", "trunk": "快速公路", "trunk_link": "快速公路匝道",
    "primary": "省道", "primary_link": "省道匝道", "secondary": "縣道", "secondary_link": "縣道匝道",
    "tertiary": "縣鄉道", "tertiary_link": "縣鄉道匝道", "unclassified": "產業道路", "residential": "巷弄",
    "living_street": "巷弄", "service": "聯絡道", "track": "農路",
}


def lanes_per_direction(highway: str, lanes: int, oneway: int) -> int:
    """OSM 的 lanes 是整條路的車道數：單行道全部同一個方向，雙向道路兩個方向各一半"""
    if oneway:
        return lanes or DEFAULT_ONEWAY_LANES.get(highway, 1)
    return max(1, lanes // 2)


def capacity_vph(highway: str, lanes: int, oneway: int) -> int:
    """這段路一個方向每小時能通過幾輛小客車"""
    n = lanes_per_direction(highway, lanes, oneway)
    if highway == "motorway":
        return MOTORWAY_LANE_VPH * n
    if n >= 2:
        speed = DRIVE_KMH.get(highway, DEFAULT_DRIVE_KMH)
        per_lane = min(MULTILANE_MAX_VPH, max(MULTILANE_MIN_VPH, MULTILANE_MIN_VPH + 10 * (speed - 50)))
        return per_lane * n
    return MINOR_ROAD_VPH if highway in MINOR_HIGHWAYS else TWO_LANE_VPH


def road_label(info: dict) -> str:
    """「台9 玉興橋」「花71」「國道5 蔣渭水高速公路」；沒有編號也沒有名字的寫道路等級"""
    highway = info.get("highway") or ""
    refs = [r.strip() for r in (info.get("ref") or "").split(";") if r.strip()]
    if highway.startswith("motorway"):
        refs = [f"國道{r}" if r[:1].isdigit() else r for r in refs]
    elif highway.startswith(("primary", "trunk")):
        # OSM 的省道編號只寫數字（9、9丙），縣鄉道則已經帶縣名（花71、宜26）
        refs = [f"台{r}" if r[:1].isdigit() else r for r in refs]
    name = info.get("name") or ""
    parts = ["/".join(refs)] if refs else []
    if name and name not in parts:
        parts.append(name)
    return " ".join(parts) or f"未命名{HIGHWAY_TEXT.get(highway, '道路')}"


def road_key(info: dict) -> str:
    """同一條路：有路線編號用編號（路名每一段都不同，台9 會從中正路一段換到二段），沒有就用路名，再沒有就用等級"""
    return info.get("ref") or info.get("name") or f"~{info.get('highway')}"


def counted_edges(edges: tuple, end_excluded_m: float = END_EXCLUDED_M) -> list[tuple[int, bool]]:
    """路線上要算進瓶頸的 (邊, 方向)：中點落在頭尾 end_excluded_m 公尺內的不算"""
    total = sum(m for _, _, m in edges)
    out, walked = [], 0.0
    for edge_id, forward, meters in edges:
        mid = walked + meters / 2
        walked += meters
        if end_excluded_m <= mid <= total - end_excluded_m:
            out.append((edge_id, bool(forward)))
    return out


def find_bottlenecks(
    flows: list[dict], paths: dict, edge_info: dict[int, dict],
    people_per_vehicle: float = PEOPLE_PER_VEHICLE, end_excluded_m: float = END_EXCLUDED_M, top_n: int = TOP_N,
) -> dict:
    """
    flows 是 plan_evacuation 的分配，paths 是 {(起點名稱, 避難所名稱): RoadPath}（要有 edges），
    edge_info 是 repo.road_edges 的結果。回傳：
      top             清空最久的路段，最多 top_n 條路。同一條路（road_key）上車流一樣的邊合成一段，
                      名稱用其中最長那條邊的，經過的橋列在 bridges（只列名字裡有「橋」的：
                      OSM 有些橋用的是路名）。車流在路上一直變（有人匯入、有人下交流道），
                      同一條路會有好幾段只差幾十輛，所以每條路只列最慢的那一段
      max_hours       最慢那段路讓所有車通過要幾小時（全部同時出發的下限）
      vehicles        算進來的車輛數（人數 ÷ 每車人數）
      local_people    路線短到整條都在頭尾 end_excluded_m 公尺內、沒經過任何算進來的路段的人數
                      （市區避難所很密，這一類不少）。他們塞不塞是巷弄與路口的事，這裡算不出來
      uncounted_people  沒有實際路線（直線估算或查詢失敗）、沒算進任何路段的人數
    """
    load: dict[tuple[int, bool], float] = {}
    uncounted = local = counted_people = 0
    for f in flows:
        path = paths.get((f["from"], f["to"]))
        if not path or not path.edges:
            uncounted += f["people"]
            continue
        keys = counted_edges(path.edges, end_excluded_m)
        if not keys:
            local += f["people"]
            continue
        counted_people += f["people"]
        for key in keys:
            load[key] = load.get(key, 0.0) + f["people"]

    groups: dict[tuple, dict] = {}
    for (edge_id, forward), people in load.items():
        info = edge_info.get(edge_id)
        if not info:
            continue
        cap = capacity_vph(info["highway"], info["lanes"], info["oneway"])
        vehicles = people / people_per_vehicle
        # 兩個方向分開：往外疏散的車流幾乎都是單向的，反方向的車道不會被同一批車佔用
        key = (road_key(info), forward, round(people), cap)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "people": round(people), "vehicles": round(vehicles), "capacity_vph": cap,
                "hours": round(vehicles / cap, 2), "length_m": 0.0, "bridge": False, "bridges": set(),
                "_longest": -1.0, "_road": road_key(info),
            }
        g["length_m"] += info["length_m"]
        if info["bridge"]:
            g["bridge"] = True
            if "橋" in info["name"]:
                g["bridges"].add(info["name"])
        # 名稱與地圖上的位置用這一群裡最長那條邊（中點）
        if info["length_m"] > g["_longest"]:
            g["_longest"] = info["length_m"]
            g.update(label=road_label(info), ref=info["ref"], name=info["name"], highway=info["highway"],
                     lat=info["lat"], lon=info["lon"])

    ranked, seen = [], set()
    for g in sorted(groups.values(), key=lambda g: (-g["hours"], -g["vehicles"], g["label"])):
        road = g.pop("_road")
        g.pop("_longest")
        g["length_m"] = round(g["length_m"])
        g["bridges"] = sorted(g["bridges"])
        if road not in seen:
            seen.add(road)
            ranked.append(g)
    return {
        "top": ranked[:top_n],
        "max_hours": ranked[0]["hours"] if ranked else 0.0,
        "vehicles": round(counted_people / people_per_vehicle),
        "local_people": local,
        "uncounted_people": uncounted,
        "people_per_vehicle": people_per_vehicle,
        "end_excluded_m": end_excluded_m,
    }
