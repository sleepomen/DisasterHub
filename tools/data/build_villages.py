"""
宜花東三縣的村里：戶籍人口與疏散起點，輸出到 data_reference/east_villages.json。

以前疏散只有 29 個鄉鎮人口重心當起點，人口是概略值，還漏了 12 個鄉鎮。一個鄉鎮的人全部從同一點出發，
秀林、卑南這種大鄉的路線與時間跟實際差很多。現在改成每個村里一個起點。

資料來源：
- 人口：內政部戶政司「村里戶數、單一年齡人口」開放資料（ODRP014，政府資料開放授權條款第 1 版），
  https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP014/{民國年月}。是戶籍人口，不是常住人口。
- 村里範圍與建物：OpenStreetMap（© OpenStreetMap contributors，ODbL 1.0）。村里界的 nat_ref
  就是內政部的 11 碼村里代碼，兩邊直接對得上。

起點位置：村里內周圍 NEIGHBOR_M 公尺內建物最多的那棟，也就是最大聚落的中心（一樣多時取離全部建物平均點最近的）。
山區的大村里幾何中心常落在沒人住的山上。第一版取「離建物平均點最近的那棟」，大同鄉茂安村只有 8 棟、
分散在山裡，選到的那棟離最近的道路 5.8 公里；兩個聚落的村里也會選到兩者中間的零星房子。
起點要接得上路網，所以只在離道路 ROAD_NEAR_M 公尺內的建物裡挑（data_reference/east_roads.json.gz，
要先建好路網）；村里裡一棟都沒有才用全部的建物。村里內沒有任何建物時，用村里界頂點的平均。

    docker run --rm -v <下載資料夾>:/in:ro -v "<專案根目錄>":/work -w /work python:3.12-slim \\
        sh -c "pip install -q osmium && python tools/build_villages.py /in/taiwan-latest.osm.pbf --yyymm 11509"
"""
import argparse
import datetime
import gzip
import json
import math
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_road_network import BBOX, EAST_COUNTIES, SOURCE, RingIndex, stitch_rings  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data_reference" / "east_villages.json"
ROADS = ROOT / "data_reference" / "east_roads.json.gz"
ROAD_NEAR_M = 1000
API = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP014/{yyymm}?page={page}"
POPULATION_SOURCE = {
    "dataset": "內政部戶政司 村里戶數、單一年齡人口（ODRP014）",
    "url": "https://www.ris.gov.tw/app/portal/3053",
    "license": "政府資料開放授權條款－第 1 版",
}
CELL_DEG = 0.02
NEIGHBOR_M = 500


def fetch_population(yyymm: str) -> dict[str, dict]:
    """{村里代碼: {county, township, village, households, pop}}，只留三縣"""
    rows, page, pages = [], 1, 1
    while page <= pages:
        with urllib.request.urlopen(API.format(yyymm=yyymm, page=page), timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
        if "responseData" not in data:
            raise SystemExit(f"{yyymm} 沒有資料：{data.get('responseMessage')}")
        pages = int(data["totalPage"])
        rows += data["responseData"]
        page += 1
    out = {}
    for r in rows:
        county = r["site_id"][:3]
        if county in EAST_COUNTIES:
            out[r["district_code"]] = {
                "county": county, "township": r["site_id"][3:], "village": r["village"],
                "households": int(r["household_no"]), "pop": int(r["people_total"]),
            }
    return out


def read_osm(path: str, codes: set[str]):
    """村里界的環（依代碼）、英文名（依代碼）與三縣外接框內每棟建物的中心點"""
    import osmium

    class Relations(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.way_code: dict[int, list[str]] = defaultdict(list)
            self.name_en: dict[str, str] = {}

        def relation(self, r):
            code = r.tags.get("nat_ref")
            if r.tags.get("admin_level") == "9" and code in codes:
                self.name_en[code] = r.tags.get("name:en", "")
                for m in r.members:
                    if m.type == "w":
                        self.way_code[m.ref].append(code)

    class Ways(osmium.SimpleHandler):
        def __init__(self, way_code):
            super().__init__()
            self.way_code = way_code
            self.segments: dict[str, list] = defaultdict(list)
            self.buildings: list[tuple[float, float]] = []

        def way(self, w):
            if w.id in self.way_code:
                nodes = [(n.ref, n.lon, n.lat) for n in w.nodes if n.location.valid()]
                for code in self.way_code[w.id]:
                    self.segments[code].append(nodes)
            if "building" in w.tags:
                coords = [(n.lon, n.lat) for n in w.nodes if n.location.valid()]
                if coords:
                    lon = sum(c[0] for c in coords) / len(coords)
                    lat = sum(c[1] for c in coords) / len(coords)
                    lon_min, lat_min, lon_max, lat_max = BBOX
                    if lon_min <= lon <= lon_max and lat_min <= lat <= lat_max:
                        self.buildings.append((lon, lat))

    relations = Relations()
    relations.apply_file(path)
    ways = Ways(relations.way_code)
    ways.apply_file(path, locations=True, idx="flex_mem")
    return ways.segments, relations.name_en, ways.buildings


def decode_polyline(text: str) -> list[tuple[float, float]]:
    """encoded polyline（precision 5，lat/lon）→ [(lon, lat)]"""
    coords, index, lat, lon = [], 0, 0, 0
    while index < len(text):
        for axis in (0, 1):
            shift = result = 0
            while True:
                b = ord(text[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if axis == 0:
                lat += delta
            else:
                lon += delta
        coords.append((lon / 1e5, lat / 1e5))
    return coords


class RoadProximity:
    """離道路頂點 ROAD_NEAR_M 公尺內嗎（以頂點近似，路網存檔時已簡化到約 5 公尺）"""

    def __init__(self, path: Path = ROADS):
        self.cell = ROAD_NEAR_M / 111_320
        self.grid: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for edge in json.load(f)["edges"]:
                for lon, lat in decode_polyline(edge[4]):
                    self.grid[(math.floor(lon / self.cell), math.floor(lat / self.cell))].append((lon, lat))

    def near(self, lon: float, lat: float) -> bool:
        k = math.cos(math.radians(lat))
        cx, cy = math.floor(lon / self.cell), math.floor(lat / self.cell)
        return any(
            ((x - lon) * k) ** 2 + (y - lat) ** 2 <= self.cell ** 2
            for dx in (-1, 0, 1) for dy in (-1, 0, 1) for x, y in self.grid.get((cx + dx, cy + dy), ())
        )


def anchor(buildings: list[tuple[float, float]]) -> tuple[float, float]:
    """周圍 NEIGHBOR_M 公尺內建物最多的那棟；一樣多時取離全部建物平均點最近的"""
    lat0 = sum(b[1] for b in buildings) / len(buildings)
    lon0 = sum(b[0] for b in buildings) / len(buildings)
    k = math.cos(math.radians(lat0))
    cell = NEIGHBOR_M / 111_320
    grid: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    for lon, lat in buildings:
        grid[(math.floor(lon * k / cell), math.floor(lat / cell))].append((lon, lat))
    limit = cell * cell

    def neighbours(b):
        cx, cy = math.floor(b[0] * k / cell), math.floor(b[1] / cell)
        return sum(
            1 for dx in (-1, 0, 1) for dy in (-1, 0, 1) for o in grid.get((cx + dx, cy + dy), ())
            if ((o[0] - b[0]) * k) ** 2 + (o[1] - b[1]) ** 2 <= limit
        )

    return min(buildings, key=lambda b: (-neighbours(b), ((b[0] - lon0) * k) ** 2 + (b[1] - lat0) ** 2))


def build(pbf: str, yyymm: str) -> dict:
    population = fetch_population(yyymm)
    segments, names_en, buildings = read_osm(pbf, set(population))
    missing = sorted(set(population) - set(segments))
    if missing:
        raise SystemExit(f"OSM 找不到這些村里的範圍：{missing[:10]}")

    # 每個村里的環與外接框；建物用格網找候選村里
    shapes, grid = {}, defaultdict(list)
    for code, segs in segments.items():
        rings = stitch_rings(segs)
        xs = [x for ring in rings for x, _ in ring]
        ys = [y for ring in rings for _, y in ring]
        box = (min(xs), min(ys), max(xs), max(ys))
        shapes[code] = (RingIndex(rings), box, rings)
        for cx in range(math.floor(box[0] / CELL_DEG), math.floor(box[2] / CELL_DEG) + 1):
            for cy in range(math.floor(box[1] / CELL_DEG), math.floor(box[3] / CELL_DEG) + 1):
                grid[(cx, cy)].append(code)

    inside: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for lon, lat in buildings:
        for code in grid.get((math.floor(lon / CELL_DEG), math.floor(lat / CELL_DEG)), ()):
            index, (x0, y0, x1, y1), _ = shapes[code]
            if x0 <= lon <= x1 and y0 <= lat <= y1 and index.contains(lon, lat):
                inside[code].append((lon, lat))
                break

    roads = RoadProximity()
    villages, off_road = [], []
    for code, info in sorted(population.items()):
        points = inside.get(code, [])
        reachable = [b for b in points if roads.near(*b)]
        if points and not reachable:
            off_road.append(info["township"] + info["village"])
        points = reachable or points
        if points:
            lon, lat = anchor(points)
        else:
            ring = max(shapes[code][2], key=len)
            lon, lat = sum(x for x, _ in ring) / len(ring), sum(y for _, y in ring) / len(ring)
        villages.append({
            "code": code, **info, "village_en": names_en.get(code, ""),
            "lat": round(lat, 5), "lon": round(lon, 5), "buildings": len(points),
        })
    no_buildings = [v["county"] + v["township"] + v["village"] for v in villages if not v["buildings"]]
    print(f"{len(villages)} 個村里、{sum(v['pop'] for v in villages):,} 人；"
          f"三縣外接框內建物 {len(buildings):,} 棟，落在村里內 {sum(len(p) for p in inside.values()):,} 棟；"
          f"沒有建物、改用村里界中心的：{len(no_buildings)} 個 {no_buildings[:8]}；"
          f"建物都離道路超過 {ROAD_NEAR_M} 公尺的：{len(off_road)} 個 {off_road[:8]}")
    return {
        "type": "villages",
        "population_source": {**POPULATION_SOURCE, "statistic_yyymm": yyymm},
        "location_source": {**SOURCE, "method": f"村里內周圍 {NEIGHBOR_M} 公尺內建物最多的一棟（最大聚落）"},
        "built": datetime.date.today().isoformat(),
        "count": len(villages),
        "villages": villages,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pbf", help="taiwan-latest.osm.pbf 的路徑")
    parser.add_argument("--yyymm", required=True, help="人口統計的民國年月，例如 11509")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    data = build(args.pbf, args.yyymm)
    Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"寫入 {args.out}（{Path(args.out).stat().st_size / 1e3:.0f} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
