"""
把 OpenStreetMap 的台灣路網裁成宜花東三縣，轉成 pgRouting 用的邊表，
輸出到 data_reference/east_roads.json.gz，應用程式啟動同步時匯入 PostGIS 的 roads 資料表。

資料來源：OpenStreetMap（© OpenStreetMap contributors），Geofabrik 每日提供的台灣切片
https://download.geofabrik.de/asia/taiwan-latest.osm.pbf 。授權是 ODbL 1.0：
這個檔案是 OSM 的衍生資料庫，散布時同樣適用 ODbL，並且要標示出處。

做法：
1. 讀出宜蘭縣、花蓮縣、臺東縣的行政界（admin_level=4 的 relation），把外框的 way 接成環。
2. 保留車輛可通行的道路（見 HIGHWAYS），首、中、末任一節點落在三縣內的整條保留。
3. 在路口（被兩條以上道路共用的節點）與道路端點切開，每一段就是一條邊；
   長度用原始幾何算，存檔的幾何再簡化，檔案才不會太大。
4. 丟掉很小的孤立片段：使用者的位置若吸附到一段不跟任何路相連的停車場車道，
   就永遠到不了任何避難所。

PBF 是 protobuf 格式，這裡用 pyosmium 讀，不自己解析。本機沒裝的話用一次性的容器跑：
    docker run --rm -v <下載資料夾>:/in:ro -v "<專案根目錄>":/work -w /work python:3.12-slim \\
        sh -c "pip install -q osmium && python tools/build_road_network.py /in/taiwan-latest.osm.pbf"
"""
import argparse
import datetime
import gzip
import json
import math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data_reference" / "east_roads.json.gz"

EAST_COUNTIES = {"宜蘭縣", "花蓮縣", "臺東縣"}
# 車輛可以走的道路。不含 footway / path / steps：疏散時以車行為主，步道也常常不連通
HIGHWAYS = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link", "tertiary", "tertiary_link",
    "unclassified", "residential", "living_street", "service", "track",
}
# 停車場內的車道、私人車道對路網連通沒有幫助，只會讓吸附點落在死路上
SKIP_SERVICE = {"parking_aisle", "driveway", "drive-through"}
SKIP_ACCESS = {"no", "private"}
# 先用外接框擋掉西部，再做精確的縣界判斷（lon_min, lat_min, lon_max, lat_max）
BBOX = (120.6, 21.8, 122.1, 25.1)
# 連通片段少於這麼多條邊就丟掉。綠島、蘭嶼本身各有上百條邊，不會被誤刪
MIN_COMPONENT_EDGES = 20
# 存檔幾何的簡化容許誤差（度，約 5 公尺）；長度在簡化前就算好，不受影響
SIMPLIFY_DEG = 0.00005
BAND_DEG = 0.01

SOURCE = {
    "dataset": "OpenStreetMap",
    "provider": "© OpenStreetMap contributors（Geofabrik taiwan-latest.osm.pbf）",
    "url": "https://download.geofabrik.de/asia/taiwan.html",
    "license": "ODbL 1.0（https://opendatacommons.org/licenses/odbl/）",
}


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def line_length_m(coords: list[tuple[float, float]]) -> float:
    return sum(haversine_m(*coords[i], *coords[i + 1]) for i in range(len(coords) - 1))


def stitch_rings(segments: list[list[tuple[int, float, float]]]) -> list[list[tuple[float, float]]]:
    """
    行政界的外框由好幾條 way 組成，方向也不一定一致。依節點編號首尾相接成封閉環。

    宜蘭與臺東的縣界有一段畫在海上（領海），落在 Geofabrik 台灣切片之外，那幾條 way 根本不在檔案裡，
    本島的環永遠接不回起點——第一版直接丟掉接不起來的殘段，結果兩縣只剩龜山島、蘭嶼這些小島，
    宜蘭市區離「最近的道路」有 45 公里。接不起來的殘段改成依端點遠近串起來，缺口用直線補上；
    缺口都在海上，直線補起來不會切掉任何道路
    """
    remaining = [list(s) for s in segments if len(s) >= 2]
    rings, open_chains = [], []
    while remaining:
        ring = remaining.pop()
        flipped = False
        while ring[0][0] != ring[-1][0]:
            tail = ring[-1][0]
            for i, seg in enumerate(remaining):
                if seg[0][0] == tail:
                    ring += seg[1:]
                elif seg[-1][0] == tail:
                    ring += seg[::-1][1:]
                else:
                    continue
                remaining.pop(i)
                break
            else:
                # 往後接不下去了，掉頭從另一端再接一次，殘段才會盡量長
                if flipped:
                    break
                ring.reverse()
                flipped = True
        if ring[0][0] == ring[-1][0]:
            if len(ring) >= 4:
                rings.append([(lon, lat) for _, lon, lat in ring])
        else:
            open_chains.append([(lon, lat) for _, lon, lat in ring])

    if open_chains:
        merged = open_chains.pop(0)
        while open_chains:
            tail = merged[-1]
            i, reverse = min(
                ((i, rev) for i in range(len(open_chains)) for rev in (False, True)),
                key=lambda c: math.dist(tail, open_chains[c[0]][-1 if c[1] else 0]),
            )
            chain = open_chains.pop(i)
            merged += chain[::-1] if reverse else chain
        merged.append(merged[0])
        rings.append(merged)
    return rings


class RingIndex:
    """
    多個環的奇偶規則點在多邊形內判斷。縣界一個環就有上萬個頂點，逐點掃全部邊太慢，
    所以依緯度切成窄帶，每個點只檢查跨過它那一帶的邊
    """

    def __init__(self, rings: list[list[tuple[float, float]]]):
        self.bands: dict[int, list[tuple[float, float, float, float]]] = defaultdict(list)
        for ring in rings:
            for (x1, y1), (x2, y2) in zip(ring, ring[1:], strict=False):
                if y1 == y2:
                    continue
                for b in range(math.floor(min(y1, y2) / BAND_DEG), math.floor(max(y1, y2) / BAND_DEG) + 1):
                    self.bands[b].append((x1, y1, x2, y2))

    def contains(self, lon: float, lat: float) -> bool:
        inside = False
        for x1, y1, x2, y2 in self.bands.get(math.floor(lat / BAND_DEG), ()):
            if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
        return inside


def in_any(indexes: list[RingIndex], coords: list[tuple[float, float]]) -> bool:
    """道路的頭、中、尾任一點落在任一縣內就算"""
    return any(index.contains(*coords[i]) for index in indexes for i in (0, len(coords) // 2, -1))


def simplify(coords: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    """Douglas-Peucker，保留首尾"""
    if len(coords) <= 2:
        return coords
    keep = [False] * len(coords)
    keep[0] = keep[-1] = True
    stack = [(0, len(coords) - 1)]
    while stack:
        start, end = stack.pop()
        (x1, y1), (x2, y2) = coords[start], coords[end]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy)
        best, best_i = 0.0, None
        for i in range(start + 1, end):
            px, py = coords[i]
            if norm == 0:
                d = math.hypot(px - x1, py - y1)
            else:
                d = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / norm
            if d > best:
                best, best_i = d, i
        if best_i is not None and best > tolerance:
            keep[best_i] = True
            stack += [(start, best_i), (best_i, end)]
    return [c for c, k in zip(coords, keep, strict=True) if k]


def encode_polyline(coords: list[tuple[float, float]]) -> str:
    """Google encoded polyline（precision 5，約 1 公尺），PostGIS 用 ST_LineFromEncodedPolyline 讀回"""
    out = []
    prev_lat = prev_lon = 0
    for lon, lat in coords:
        ilat, ilon = round(lat * 1e5), round(lon * 1e5)
        for delta in (ilat - prev_lat, ilon - prev_lon):
            v = ~(delta << 1) if delta < 0 else delta << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev_lat, prev_lon = ilat, ilon
    return "".join(out)


def split_at_junctions(ways: list[dict]) -> list[dict]:
    """
    每條 way 在路口切開。路口是被兩條以上 way 共用（或同一條 way 經過兩次）的節點，
    way 的首尾也一律當作切點，pgRouting 的邊才會在真正的交叉處相連
    """
    usage: dict[int, int] = defaultdict(int)
    for w in ways:
        for ref in w["refs"]:
            usage[ref] += 1
        usage[w["refs"][0]] += 1
        usage[w["refs"][-1]] += 1
    edges = []
    for w in ways:
        refs, coords = w["refs"], w["coords"]
        start = 0
        for i in range(1, len(refs)):
            if usage[refs[i]] >= 2 or i == len(refs) - 1:
                part = coords[start:i + 1]
                length = line_length_m(part)
                if length > 0:
                    edges.append({"source": refs[start], "target": refs[i], "length": length,
                                  "highway": w["highway"], "coords": part})
                start = i
    return edges


def drop_small_components(edges: list[dict], min_edges: int) -> list[dict]:
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in edges:
        a, b = find(e["source"]), find(e["target"])
        if a != b:
            parent[a] = b
    size: dict[int, int] = defaultdict(int)
    for e in edges:
        size[find(e["source"])] += 1
    return [e for e in edges if size[find(e["source"])] >= min_edges]


def renumber(edges: list[dict]) -> int:
    """OSM 節點編號很大又不連續，換成 1..N，pgRouting 的頂點編號才緊湊"""
    ids: dict[int, int] = {}
    for e in edges:
        for key in ("source", "target"):
            e[key] = ids.setdefault(e[key], len(ids) + 1)
    return len(ids)


def read_osm(path: str):
    import osmium

    class Boundaries(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.way_ids: dict[int, str] = {}

        def relation(self, r):
            if (r.tags.get("boundary") == "administrative" and r.tags.get("admin_level") == "4"
                    and r.tags.get("name") in EAST_COUNTIES):
                for m in r.members:
                    if m.type == "w" and m.role in ("outer", ""):
                        self.way_ids[m.ref] = r.tags.get("name")

    class Ways(osmium.SimpleHandler):
        def __init__(self, boundary_ids):
            super().__init__()
            self.boundary_ids = boundary_ids
            self.boundary: dict[str, list] = defaultdict(list)
            self.roads: list[dict] = []

        def way(self, w):
            if w.id in self.boundary_ids:
                nodes = [(n.ref, n.lon, n.lat) for n in w.nodes if n.location.valid()]
                self.boundary[self.boundary_ids[w.id]].append(nodes)
            tags = w.tags
            highway = tags.get("highway")
            if highway not in HIGHWAYS or tags.get("area") == "yes":
                return
            if tags.get("service") in SKIP_SERVICE or tags.get("access") in SKIP_ACCESS:
                return
            refs, coords = [], []
            for n in w.nodes:
                if not n.location.valid():
                    continue
                refs.append(n.ref)
                coords.append((n.lon, n.lat))
            if len(refs) < 2:
                return
            lon_min, lat_min, lon_max, lat_max = BBOX
            if not any(lon_min <= lon <= lon_max and lat_min <= lat <= lat_max for lon, lat in coords):
                return
            self.roads.append({"refs": refs, "coords": coords, "highway": highway})

    boundaries = Boundaries()
    boundaries.apply_file(path)
    if set(boundaries.way_ids.values()) != EAST_COUNTIES:
        raise SystemExit(f"找不到完整的三縣行政界：只找到 {sorted(set(boundaries.way_ids.values()))}")
    ways = Ways(boundaries.way_ids)
    ways.apply_file(path, locations=True, idx="flex_mem")
    return ways.boundary, ways.roads


def build(path: str) -> dict:
    boundary, roads = read_osm(path)
    # 每個縣各自一個索引，道路落在任一縣內就留下。不能把三縣的環丟進同一個奇偶索引：
    # 臺東縣界補上海上缺口後的直線會切進花蓮南部，富里、玉里同時落在花蓮與臺東的環裡，
    # 奇偶規則翻兩次變成「不在任何縣內」——第一版因此整個富里縱谷沒有道路
    indexes = []
    for county, segments in sorted(boundary.items()):
        county_rings = stitch_rings(segments)
        print(f"{county}：{len(segments)} 條界線 → {len(county_rings)} 個封閉環")
        indexes.append(RingIndex(county_rings))

    kept = [w for w in roads if in_any(indexes, w["coords"])]
    print(f"外接框內道路 {len(roads)} 條，三縣內 {len(kept)} 條")

    edges = split_at_junctions(kept)
    before = len(edges)
    edges = drop_small_components(edges, MIN_COMPONENT_EDGES)
    nodes = renumber(edges)
    total_km = sum(e["length"] for e in edges) / 1000
    print(f"切成 {before} 條邊，丟掉小片段後 {len(edges)} 條、{nodes} 個頂點、總長 {total_km:.0f} 公里")

    return {
        "type": "road_network",
        "source": {**SOURCE, "built": datetime.date.today().isoformat()},
        "counts": {"nodes": nodes, "edges": len(edges), "km": round(total_km)},
        # [source, target, 長度（公尺）, 道路等級, encoded polyline]
        "edges": [
            [e["source"], e["target"], round(e["length"], 1), e["highway"],
             encode_polyline(simplify(e["coords"], SIMPLIFY_DEG))]
            for e in edges
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pbf", help="taiwan-latest.osm.pbf 的路徑")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    network = build(args.pbf)
    with gzip.open(args.out, "wt", encoding="utf-8") as f:
        json.dump(network, f, ensure_ascii=False, separators=(",", ":"))
    print(f"寫入 {args.out}（{Path(args.out).stat().st_size / 1e6:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
