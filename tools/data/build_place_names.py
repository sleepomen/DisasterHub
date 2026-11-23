"""
把 OpenStreetMap 的地名點（村落、部落、社區、海灣、湖、山、景點…）裁成宜花東三縣，
輸出到 data_reference/east_places.json，給「知本附近有避難所嗎」這類問句把小地名換成座標用。

鄉鎮、縣市名稱不收：規則層本來就認得（services/chat/query_rules.py 的 TOWNSHIPS）。
避難所資料的村里名稱另外由應用程式補上（services/geo/places.py），這裡只處理 OSM。

資料來源與授權跟路網相同：OpenStreetMap（© OpenStreetMap contributors），Geofabrik 台灣切片，
ODbL 1.0，散布時要標示出處。縣界判斷沿用 tools/build_road_network.py 的做法。

    docker run --rm -v <下載資料夾>:/in:ro -v "<專案根目錄>":/work -w /work python:3.12-slim \\
        sh -c "apt-get update -qq && apt-get install -y -qq libexpat1 >/dev/null && pip install -q osmium \\
               && python tools/build_place_names.py /in/taiwan-latest.osm.pbf"
"""
import argparse
import datetime
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_road_network import BBOX, EAST_COUNTIES, SOURCE, RingIndex, stitch_rings  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data_reference" / "east_places.json"

# 人會拿來當「我在哪裡」的地名。place=city / town 幾乎都是鄉鎮名，規則層已經認得，不收
PLACE_TAGS = {
    "place": {"village", "hamlet", "suburb", "quarter", "neighbourhood", "locality", "isolated_dwelling", "islet"},
    "natural": {"peak", "bay", "beach", "cape", "water", "hot_spring", "valley", "cliff", "spring", "wetland"},
    "tourism": {"attraction", "viewpoint"},
    "leisure": {"nature_reserve"},
    "boundary": {"national_park", "protected_area"},
}
# 問句裡常常省略的字尾：「馬太鞍部落」問的人只說「馬太鞍」
STRIP_SUFFIXES = ("部落", "聚落", "社區", "國家公園", "國家風景區", "風景區", "遊憩區")
MIN_FORM_LENGTH = 2


def place_kind(tags) -> str | None:
    for key, values in PLACE_TAGS.items():
        value = tags.get(key)
        if value in values:
            return f"{key}={value}"
    return None


def forms_of(name: str) -> list[str]:
    forms = [name]
    for suffix in STRIP_SUFFIXES:
        if name.endswith(suffix) and len(name) - len(suffix) >= MIN_FORM_LENGTH:
            forms.append(name[: -len(suffix)])
    return forms


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

    class Places(osmium.SimpleHandler):
        def __init__(self, boundary_ids):
            super().__init__()
            self.boundary_ids = boundary_ids
            self.boundary: dict[str, list] = defaultdict(list)
            self.places: list[dict] = []

        def _add(self, tags, lon, lat):
            name = tags.get("name:zh") or tags.get("name")
            kind = place_kind(tags)
            if not name or not kind:
                return
            lon_min, lat_min, lon_max, lat_max = BBOX
            if lon_min <= lon <= lon_max and lat_min <= lat <= lat_max:
                self.places.append({"name": name.strip(), "kind": kind, "lon": lon, "lat": lat})

        def node(self, n):
            if n.location.valid():
                self._add(n.tags, n.location.lon, n.location.lat)

        def way(self, w):
            if w.id in self.boundary_ids:
                nodes = [(n.ref, n.lon, n.lat) for n in w.nodes if n.location.valid()]
                self.boundary[self.boundary_ids[w.id]].append(nodes)
            if not place_kind(w.tags):
                return
            coords = [(n.lon, n.lat) for n in w.nodes if n.location.valid()]
            if coords:
                # 湖、海灣、景點範圍：用頂點平均當代表點，問「附近」夠用
                self._add(w.tags, sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords))

    boundaries = Boundaries()
    boundaries.apply_file(path)
    if set(boundaries.way_ids.values()) != EAST_COUNTIES:
        raise SystemExit(f"找不到完整的三縣行政界：只找到 {sorted(set(boundaries.way_ids.values()))}")
    places = Places(boundaries.way_ids)
    places.apply_file(path, locations=True, idx="flex_mem")
    return places.boundary, places.places


def build(path: str, township_names: set[str]) -> dict:
    boundary, raw = read_osm(path)
    indexes = [RingIndex(stitch_rings(segments)) for _, segments in sorted(boundary.items())]
    inside = [p for p in raw if any(index.contains(p["lon"], p["lat"]) for index in indexes)]
    kept, seen = [], set()
    for p in inside:
        forms = [f for f in forms_of(p["name"]) if len(f) >= MIN_FORM_LENGTH and f not in township_names]
        key = (p["name"], round(p["lon"], 3), round(p["lat"], 3))
        if not forms or key in seen:
            continue
        seen.add(key)
        kept.append({"name": p["name"], "forms": forms, "kind": p["kind"],
                     "lat": round(p["lat"], 6), "lon": round(p["lon"], 6)})
    kept.sort(key=lambda p: (p["name"], p["lat"]))
    print(f"外接框內 {len(raw)} 個地名點，三縣內 {len(inside)} 個，去掉鄉鎮名與重複後 {len(kept)} 個")
    return {
        "type": "place_names",
        "source": {**SOURCE, "built": datetime.date.today().isoformat()},
        "count": len(kept),
        "places": kept,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pbf", help="taiwan-latest.osm.pbf 的路徑")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    # 鄉鎮全名與去掉「鄉鎮市」的簡稱都不收（「玉里」「池上」規則層已經認得）
    sys.path.insert(0, str(ROOT))
    from services.retrieval.shelter_profile import TOWNSHIPS

    townships = {t for ts in TOWNSHIPS.values() for t in ts}
    names = townships | {t[:-1] for t in townships if len(t) > 2} | {t.replace("台", "臺") for t in townships}
    names |= {"宜蘭", "花蓮", "台東", "臺東", "宜蘭縣", "花蓮縣", "台東縣", "臺東縣"}
    network = build(args.pbf, names)
    Path(args.out).write_text(json.dumps(network, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"寫入 {args.out}（{Path(args.out).stat().st_size / 1e3:.0f} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
