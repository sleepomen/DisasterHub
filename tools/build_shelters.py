"""
把消防署「避難收容處所點位檔」（CSV）轉成 data_for_refuge/ 的三個縣檔（宜蘭、花蓮、臺東），
應用程式啟動同步時由 DataFetcher 讀進 PostGIS 與向量索引。

資料來源：政府資料開放平台「避難收容處所點位檔」（內政部消防署），
政府資料開放授權條款第 1 版。https://data.gov.tw/dataset/73242

清理規則（每一條都是實際在資料裡看到的狀況）：
- 座標不在所屬縣的範圍內就丟掉：澳花國小被放在新竹山區、礁溪鄉公所在福建、長濱三間活動中心在臺北市
- 座標離同鄉鎮其他避難所的中位位置太遠也丟掉：卑南鄉賓朗村活動中心被放在花蓮（緯度多了 0.7 度）
- 上面兩種之中，名稱與地址都能在 OpenStreetMap 對上的，用 data_reference/shelter_coordinate_fixes.json
  修正後保留；對不上的（例如地址差一個門牌）寧可不收，不猜
- 管理人姓名不收（個人資料）；電話是處所的聯絡窗口，保留
- 名稱或地址後面夾英文（「介達國小 Jieda Elementary School」）的去掉英文
- 同一縣裡同名的（各鄉都有「建業社區活動中心」）在名稱後面加上鄉鎮，名稱在資料庫裡是唯一鍵
- 地址補上縣市鄉鎮；只寫「1鄰6號」的再補上村里

用法（下載的檔案放在專案外的獨立資料夾）：
    python tools/build_shelters.py /path/to/避難收容處所點位檔.csv
"""
import argparse
import csv
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data_for_refuge"
FIXES = ROOT / "data_reference" / "shelter_coordinate_fixes.json"

# 縣 → (輸出檔名, 經度下限, 緯度下限, 經度上限, 緯度上限)。範圍含離島（龜山島、綠島、蘭嶼）
COUNTIES = {
    "宜蘭縣": ("yilan_shelter.json", 121.3, 24.3, 122.0, 25.0),
    "花蓮縣": ("hualien_shelter.json", 121.0, 23.0, 121.8, 24.4),
    "臺東縣": ("taitung_shelter.json", 120.7, 21.9, 121.6, 23.5),
}
# 離同鄉鎮其他避難所的中位位置超過這個距離就丟掉。秀林、海端這種大鄉裡正常的避難所最遠約 30 公里；
# 實際抓到的三筆都差了 46～77 公里（緯度打錯一位數、放到隔壁縣）
TOWNSHIP_OUTLIER_KM = 40

LATIN_TAIL = re.compile(r"\s*[A-Za-z].*$")
COUNTY_PREFIX = re.compile(r"^(宜蘭縣|花蓮縣|臺東縣|台東縣)")


def flag(value: str) -> bool | None:
    return {"是": True, "否": False}.get(value.strip())


def strip_latin(text: str) -> str:
    """去掉中文後面夾的英文；整串都是英文就原樣保留"""
    stripped = LATIN_TAIL.sub("", text).strip()
    return stripped or text.strip()


def full_address(county_town: str, village: str, address: str, towns: set[str]) -> str:
    """towns 是同一縣所有鄉鎮；地址自己寫了鄉鎮（不一定是所屬那一個，卑南鄉有一筆寫台東市）就只補縣"""
    address = strip_latin(address)
    if COUNTY_PREFIX.match(address):
        return COUNTY_PREFIX.sub(lambda m: m.group(1).replace("台東縣", "臺東縣"), address)
    if any(address.startswith(town) for town in towns):
        return county_town[:3] + address
    # 只有「1鄰6號」「5鄰105號」這種寫法時，沒有村里就找不到地方
    if village and re.match(r"^\d+鄰", address):
        return county_town + village + address
    return county_town + address


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    # 只拿來找離群值，等距矩形近似就夠
    return (((lat1 - lat2) * 111.2) ** 2 + ((lon1 - lon2) * 101.7) ** 2) ** 0.5


def build(rows: list[dict], fixes: dict) -> tuple[dict[str, list[dict]], list[str]]:
    """回傳 ({縣: [避難所]}, 丟掉的說明)"""
    by_county: dict[str, list[dict]] = defaultdict(list)
    dropped = []
    towns: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        county_town = row["縣市及鄉鎮市區"].strip().replace("台東縣", "臺東縣")
        if county_town[3:]:
            towns[county_town[:3]] |= {county_town[3:], county_town[3:].replace("臺", "台")}
    for row in rows:
        county_town = row["縣市及鄉鎮市區"].strip().replace("台東縣", "臺東縣")
        county = county_town[:3]
        if county not in COUNTIES:
            continue
        raw_name = row["避難收容處所名稱"].strip()
        key = f"{county_town}|{raw_name}"
        try:
            lat, lon = float(row["緯度"]), float(row["經度"])
        except ValueError:
            lat = lon = None
        if key in fixes:
            lat, lon = fixes[key]["lat"], fixes[key]["lon"]
        _, lon_min, lat_min, lon_max, lat_max = COUNTIES[county]
        if lat is None or not (lon_min <= lon <= lon_max and lat_min <= lat <= lat_max):
            dropped.append(f"{key}（{row['緯度']}, {row['經度']}）座標不在{county}內")
            continue
        try:
            capacity = int(float(row["預計收容人數"] or 0))
        except ValueError:
            capacity = 0
        village = row["村里"].strip()
        by_county[county].append({
            "name": strip_latin(raw_name),
            "total_vessel": capacity,
            "total_people": 0,
            "address": full_address(county_town, village, row["避難收容處所地址"].strip(), towns[county]),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "township": county_town[3:],
            "village": village,
            "hazards": [h.strip() for h in row["適用災害類別"].split(",") if h.strip()],
            "indoor": flag(row["室內"]),
            "outdoor": flag(row["室外"]),
            "vulnerable_ok": flag(row["適合避難弱者安置"]),
            "phone": row["管理人電話"].strip(),
            "serves": row["預計收容村里"].strip(),
        })

    for county, shelters in list(by_county.items()):
        by_town = defaultdict(list)
        for s in shelters:
            by_town[s["township"]].append(s)
        outliers = []
        for town, members in by_town.items():
            mid_lat = statistics.median(s["lat"] for s in members)
            mid_lon = statistics.median(s["lon"] for s in members)
            for s in members:
                d = distance_km(s["lat"], s["lon"], mid_lat, mid_lon)
                if d > TOWNSHIP_OUTLIER_KM:
                    outliers.append(id(s))
                    dropped.append(f"{county}{town}|{s['name']}（{s['lat']}, {s['lon']}）離同鄉鎮其他避難所 {d:.0f} 公里")
        shelters = by_county[county] = [s for s in shelters if id(s) not in outliers]

        # 同名的加上鄉鎮；同一個鄉鎮裡還同名就再加村里
        counts = Counter(s["name"] for s in shelters)
        for s in shelters:
            if counts[s["name"]] > 1:
                s["name"] = f"{s['name']}（{s['township']}）"
        counts = Counter(s["name"] for s in shelters)
        for s in shelters:
            if counts[s["name"]] > 1:
                s["name"] = f"{s['name'][:-1]}{s['village']}）"
        if len({s["name"] for s in shelters}) != len(shelters):
            raise SystemExit(f"{county} 加上鄉鎮村里之後仍有同名避難所")
        shelters.sort(key=lambda s: (s["township"], s["name"]))
    return by_county, dropped


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_path")
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    args = parser.parse_args()

    with open(args.csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    fixes = json.loads(FIXES.read_text(encoding="utf-8"))["fixes"]
    by_county, dropped = build(rows, fixes)

    out_dir = Path(args.out_dir)
    for county, (filename, *_) in COUNTIES.items():
        shelters = by_county.get(county, [])
        # 一筆一行：重建後的 diff 直接看得出哪幾間變了
        text = "[\n" + ",\n".join(json.dumps(s, ensure_ascii=False) for s in shelters) + "\n]\n"
        (out_dir / filename).write_text(text, encoding="utf-8", newline="\n")
        print(f"{filename}: {len(shelters)} 筆")
    print(f"修正座標 {len(fixes)} 筆（{FIXES.name}）")
    print(f"丟掉 {len(dropped)} 筆：")
    for line in dropped:
        print("  " + line)


if __name__ == "__main__":
    main()
