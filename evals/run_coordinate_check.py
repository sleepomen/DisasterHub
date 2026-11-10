"""
避難所座標檢查：路線是從「避難所吸附到的那條路」開始算的，座標錯了路線就跟著錯。

建資料時（tools/build_shelters.py）已經擋掉明顯的錯誤：縣外的座標、離同鄉鎮其他避難所 40 公里以上的，
650 筆裡挑出 10 筆，4 筆在 OpenStreetMap 找到名稱與地址都對得上的位置修正後保留，6 筆不收。
這裡檢查剩下 644 間在路網上的情況：
1. 離最近道路多遠：超過 ROAD_SNAP_M（1 公里）就吸附不到，那間的距離退回直線估算；超過 200 公尺列出來人工看。
2. 座標跟它自己登記的村里對不對得上：離那個村里的疏散起點（村里裡建物最密的地方）超過 5 公里的列出來，
   代表座標或村里欄位其中一個可能有誤（山區的大村里本來就大，不一定是錯）。

    docker exec disaster_app python evals/run_coordinate_check.py
"""
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from repositories.shelter_repository import ROAD_SNAP_M, ShelterRepository  # noqa: E402
from services.simulation.population import PopulationModel, distance_km  # noqa: E402

RESULTS = os.path.join(ROOT, "evals", "results", "coordinate_check.json")
FIXES = os.path.join(ROOT, "data_reference", "shelter_coordinate_fixes.json")
FAR_FROM_ROAD_M = 200
FAR_FROM_VILLAGE_KM = 5


def main() -> int:
    repo, model = ShelterRepository(), PopulationModel()
    shelters = repo.get_all_shelters()
    with repo._cursor() as cursor:
        cursor.execute(ShelterRepository.SNAP_SQL,
                       (list(range(len(shelters))), [s.lon for s in shelters], [s.lat for s in shelters]))
        to_road = {i: dist for i, _, _, dist in cursor.fetchall()}

    meters = sorted(to_road.values())
    far = sorted(
        ({"name": shelters[i].name, "township": shelters[i].township, "road_m": round(d)} for i, d in to_road.items()
         if d > FAR_FROM_ROAD_M),
        key=lambda r: -r["road_m"],
    )

    villages = {(v["township"].replace("臺", "台"), v["village"]): v for v in model.villages}
    checked, mismatched = 0, []
    for s in shelters:
        v = villages.get(((s.township or "").replace("臺", "台"), s.village or ""))
        if not v:
            continue
        checked += 1
        d = distance_km(s.lat, s.lon, v["lat"], v["lon"])
        if d > FAR_FROM_VILLAGE_KM:
            mismatched.append({"name": s.name, "village": v["name"], "km": round(d, 1)})
    mismatched.sort(key=lambda r: -r["km"])

    with open(FIXES, encoding="utf-8") as f:
        fixed = len(json.load(f)["fixes"])
    summary = {
        "shelters": len(shelters),
        "fixed_at_build": fixed,
        "road": {
            "median_m": round(statistics.median(meters)), "p90_m": round(statistics.quantiles(meters, n=10)[8]),
            "max_m": round(meters[-1]), f"over_{FAR_FROM_ROAD_M}_m": len(far),
            f"over_snap_limit_{ROAD_SNAP_M}_m": sum(m > ROAD_SNAP_M for m in meters),
        },
        "village": {"checked": checked, "unmatched_label": len(shelters) - checked,
                    f"over_{FAR_FROM_VILLAGE_KM}_km": len(mismatched)},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n離道路超過 {FAR_FROM_ROAD_M} 公尺：")
    for r in far:
        print(f"  {r['road_m']:>5} m  {r['township']}  {r['name']}")
    print(f"\n離自己登記的村里超過 {FAR_FROM_VILLAGE_KM} 公里：")
    for r in mismatched:
        print(f"  {r['km']:>5} km  {r['village']}  {r['name']}")
    os.makedirs(os.path.dirname(RESULTS), exist_ok=True)
    with open(RESULTS, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "far_from_road": far, "far_from_own_village": mismatched}, f, ensure_ascii=False, indent=2)
    print(f"\nsaved {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
