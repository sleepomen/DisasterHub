"""
把農業部「土石流潛勢溪流影響範圍圖」的 SHP 轉成東部三縣的 GeoJSON（WGS84），
輸出到 data_reference/，應用程式啟動同步時匯入 PostGIS 的 hazard_zones 資料表。

資料來源：政府資料開放平台「115年度1753條土石流潛勢溪流影響範圍圖」（農業部），
政府資料開放授權條款第 1 版。https://data.gov.tw/dataset/176526

不靠 GDAL / pyshp：SHP 與 DBF 的格式很單純，直接讀；座標從 TWD97 二度分帶（EPSG:3826）
用橫麥卡托反算公式轉回經緯度。TWD97 用的是 GRS80 橢球，跟 WGS84 的差距在公分等級，不做基準轉換。

用法（下載的檔案放在專案外的獨立資料夾，解壓後指向 .shp 去掉副檔名的路徑）：
    python tools/build_hazard_zones.py /path/to/debris1753_20260126_twd97
"""
import argparse
import datetime
import json
import math
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data_reference" / "debris_flow_east.geojson"

EAST_COUNTIES = {"宜蘭縣", "花蓮縣", "臺東縣", "台東縣"}
SOURCE = {
    "dataset": "115年度1753條土石流潛勢溪流影響範圍圖",
    "provider": "農業部",
    "url": "https://data.gov.tw/dataset/176526",
    "license": "政府資料開放授權條款第1版",
}

# TWD97 / TM2 zone 121（EPSG:3826）
A = 6378137.0
F = 1 / 298.257222101
E2 = 2 * F - F * F
EP2 = E2 / (1 - E2)
K0 = 0.9999
LON0 = math.radians(121.0)
FALSE_EASTING = 250000.0


def twd97_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """橫麥卡托反算（Snyder, Map Projections — A Working Manual, 式 8-18 起），回傳 (lon, lat)"""
    m = y / K0
    mu = m / (A * (1 - E2 / 4 - 3 * E2 ** 2 / 64 - 5 * E2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - E2)) / (1 + math.sqrt(1 - E2))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
        + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
        + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
        + (1097 * e1 ** 4 / 512) * math.sin(8 * mu)
    )
    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    c1 = EP2 * cos1 ** 2
    t1 = tan1 ** 2
    n1 = A / math.sqrt(1 - E2 * sin1 ** 2)
    r1 = A * (1 - E2) / (1 - E2 * sin1 ** 2) ** 1.5
    d = (x - FALSE_EASTING) / (n1 * K0)
    lat = phi1 - (n1 * tan1 / r1) * (
        d ** 2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * EP2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * EP2 - 3 * c1 ** 2) * d ** 6 / 720
    )
    lon = LON0 + (
        d
        - (1 + 2 * t1 + c1) * d ** 3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * EP2 + 24 * t1 ** 2) * d ** 5 / 120
    ) / cos1
    return math.degrees(lon), math.degrees(lat)


def read_dbf(path: Path) -> list[dict]:
    data = path.read_bytes()
    count, header_len, record_len = struct.unpack("<IHH", data[4:12])
    fields, pos = [], 32
    while data[pos] != 0x0D:
        name = data[pos:pos + 11].split(b"\0")[0].decode("ascii")
        fields.append((name, data[pos + 16]))
        pos += 32
    rows = []
    for i in range(count):
        offset = header_len + i * record_len + 1  # 第一個位元組是刪除旗標
        row = {}
        for name, width in fields:
            # 固定寬度欄位會把多位元組字從中間切斷，尾巴那半個字丟掉
            row[name] = data[offset:offset + width].decode("utf-8", "ignore").strip()
            offset += width
        rows.append(row)
    return rows


def read_polygons(path: Path) -> list[list[list[tuple[float, float]]]]:
    """每筆記錄回傳它的所有環（座標仍是 TWD97）"""
    data = path.read_bytes()
    pos, records = 100, []
    while pos < len(data):
        _, content_len = struct.unpack(">ii", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + content_len * 2]
        pos += 8 + content_len * 2
        shape_type = struct.unpack("<i", body[:4])[0]
        if shape_type == 0:
            records.append([])
            continue
        if shape_type != 5:
            raise ValueError(f"只支援 Polygon（5），這筆是 {shape_type}")
        num_parts, num_points = struct.unpack("<ii", body[36:44])
        parts = list(struct.unpack(f"<{num_parts}i", body[44:44 + 4 * num_parts])) + [num_points]
        coords = struct.unpack(f"<{2 * num_points}d", body[44 + 4 * num_parts:44 + 4 * num_parts + 16 * num_points])
        points = list(zip(coords[0::2], coords[1::2], strict=True))
        records.append([points[parts[i]:parts[i + 1]] for i in range(num_parts)])
    return records


def signed_area(ring) -> float:
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:], strict=False)) / 2


def to_multipolygon(rings) -> list:
    """
    SHP 的外環是順時針、內環（洞）是逆時針；洞掛在它前面最近的外環上。
    輸出依 RFC 7946 改成外環逆時針、洞順時針
    """
    polygons = []
    for ring in rings:
        lonlat = [[round(c, 6) for c in twd97_to_wgs84(x, y)] for x, y in ring]
        if signed_area(ring) < 0 or not polygons:
            polygons.append([lonlat[::-1]])
        else:
            polygons[-1].append(lonlat[::-1])
    return polygons


def build(base: Path) -> dict:
    rows = read_dbf(base.with_suffix(".dbf"))
    shapes = read_polygons(base.with_suffix(".shp"))
    if len(rows) != len(shapes):
        raise ValueError(f"DBF {len(rows)} 筆與 SHP {len(shapes)} 筆對不上")
    features = []
    for row, rings in zip(rows, shapes, strict=True):
        if row["County"] not in EAST_COUNTIES or not rings:
            continue
        features.append({
            "type": "Feature",
            "properties": {
                "debris_no": row["Debrisno"],
                "county": row["County"].replace("臺", "台"),
                "town": row["Town"],
                "village": row["Vill"],
                "risk": row["Risk"],
                "households": int(row["Total_Res"] or 0),
            },
            "geometry": {"type": "MultiPolygon", "coordinates": to_multipolygon(rings)},
        })
    return {
        "type": "FeatureCollection",
        "source": {**SOURCE, "built": datetime.date.today().isoformat()},
        "features": features,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("shapefile", help="不含副檔名的 SHP 路徑")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    collection = build(Path(args.shapefile))
    Path(args.out).write_text(json.dumps(collection, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(collection['features'])} 筆 → {args.out}")


if __name__ == "__main__":
    main()
