import json
import logging
import math
from itertools import pairwise
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_FILE = Path(__file__).resolve().parents[2] / "data_reference" / "east_taiwan_population.json"
# 村里戶籍人口與疏散起點（tools/build_villages.py）：內政部 ODRP014 ＋ OSM 村里界與建物
VILLAGE_FILE = Path(__file__).resolve().parents[2] / "data_reference" / "east_villages.json"

# 各災害類型的疏散比例（受影響人口中需要進避難所的比例），與前端動畫共用同一組數字
EVAC_RATIO = {"earthquake": 0.12, "flood": 0.22, "fire": 0.05}
DEFAULT_EVAC_RATIO = 0.12

# 距中心多遠仍算完全受影響：weight = pop * clamp(1.15 - d / radius, 0, 1)
COVERAGE_SLACK = 1.15

# 海岸線往內陸縮的緩衝（度），避免把海灘邊緣算成陸地
COAST_MARGIN_DEG = 0.004

# 半徑內完全沒有村里起點時的保底估算：每平方公里 2 人
FALLBACK_DENSITY = 2


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    )
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class PopulationModel:
    """
    東台灣的人口與海岸線模型。同一份資料同時餵給前端動畫與 AI 助手，
    模擬時「圈內覆蓋多少人、預估疏散多少人」兩邊才會講同一個數字。

    人口以村里為單位（554 個，內政部戶籍人口），每個村里是一個疏散起點。
    鄉鎮（41 個）由村里加總：人口相加，位置是各村里起點依人口加權的平均，只用來摘要與當評測的模擬中心
    """

    def __init__(self, data_file: Path | str | None = None, village_file: Path | str | None = None):
        self.data_file = Path(data_file) if data_file else DATA_FILE
        self.village_file = Path(village_file) if village_file else VILLAGE_FILE
        self.coastline: list[list[float]] = []
        self.villages: list[dict] = []
        self.townships: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.data_file.read_text(encoding="utf-8"))
            self.coastline = [[float(lat), float(lon)] for lat, lon in raw.get("coastline", [])]
        except (OSError, ValueError) as e:
            logger.error("海岸線載入失敗（%s）：%s，陸地判定全部當成陸地", self.data_file, e)
        try:
            raw = json.loads(self.village_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.error("村里人口載入失敗（%s）：%s，疏散估算將退回面積保底值", self.village_file, e)
            return
        self.villages = [
            {
                "name": f"{v['township']}{v['village']}",
                "county": str(v["county"]),
                "township": str(v["township"]),
                "village": str(v["village"]),
                "lat": float(v["lat"]),
                "lon": float(v["lon"]),
                "pop": int(v["pop"]),
            }
            for v in raw.get("villages", [])
        ]
        self.townships = self._townships(self.villages)
        logger.info("人口模型載入完成：%d 個村里、%d 個鄉鎮、%d 個海岸線節點",
                    len(self.villages), len(self.townships), len(self.coastline))

    @staticmethod
    def _townships(villages: list[dict]) -> list[dict]:
        groups: dict[tuple[str, str], list[dict]] = {}
        for v in villages:
            groups.setdefault((v["county"], v["township"]), []).append(v)
        out = []
        for (county, township), vs in groups.items():
            pop = sum(v["pop"] for v in vs)
            total = sum(v["pop"] or 1 for v in vs)
            out.append({
                "name": township, "county": county, "pop": pop,
                "lat": round(sum(v["lat"] * (v["pop"] or 1) for v in vs) / total, 5),
                "lon": round(sum(v["lon"] * (v["pop"] or 1) for v in vs) / total, 5),
            })
        return out

    def to_dict(self) -> dict:
        return {"coastline": self.coastline, "townships": self.townships}

    # ── 陸地判定 ──────────────────────────────────────────────
    def coast_lon_at(self, lat: float) -> float | None:
        c = self.coastline
        if not c:
            return None
        if lat >= c[0][0]:
            return c[0][1]
        if lat <= c[-1][0]:
            return c[-1][1]
        for (lat_a, lon_a), (lat_b, lon_b) in pairwise(c):
            if lat_b <= lat <= lat_a:
                t = (lat_a - lat) / (lat_a - lat_b) if lat_a != lat_b else 0.0
                return lon_a + (lon_b - lon_a) * t
        return c[-1][1]

    def is_land(self, lat: float, lon: float) -> bool:
        coast = self.coast_lon_at(lat)
        if coast is None:
            return True
        return lon <= coast - COAST_MARGIN_DEG

    # ── 疏散估算 ──────────────────────────────────────────────
    def covered_villages(self, lat: float, lon: float, radius_km: float) -> list[dict]:
        """半徑內的村里與其受影響人口（依起點距離衰減），權重大的排前面，同權重依名稱"""
        out = []
        for v in self.villages:
            d = distance_km(v["lat"], v["lon"], lat, lon)
            factor = max(0.0, min(1.0, COVERAGE_SLACK - d / radius_km)) if radius_km > 0 else 0.0
            weight = int(round(v["pop"] * factor))
            if weight <= 0:
                continue
            out.append({**v, "distance_km": round(d, 1), "weight": weight})
        out.sort(key=lambda v: (-v["weight"], v["name"]))
        return out

    @staticmethod
    def summarize_townships(villages: list[dict]) -> list[dict]:
        """受影響村里依鄉鎮加總（摘要「主要為○○鄉」用），權重大的排前面"""
        totals: dict[tuple[str, str], int] = {}
        for v in villages:
            key = (v["county"], v["township"])
            totals[key] = totals.get(key, 0) + v["weight"]
        return [
            {"name": township, "county": county, "weight": weight}
            for (county, township), weight in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0][1]))
        ]

    def estimate(
        self,
        lat: float,
        lon: float,
        radius_km: float,
        disaster_type: str,
        total_remaining: int | None = None,
    ) -> dict:
        """
        回傳模擬圈內的人口摘要：
        covered_population  受影響人口（依距離衰減加總）
        estimated_evacuees  依災害類型比例估出的疏散需求
        placeable / shortfall  給定範圍內避難所剩餘空間時，實際可安置與缺口
        """
        covered = self.covered_villages(lat, lon, radius_km)
        covered_population = sum(v["weight"] for v in covered)
        ratio = EVAC_RATIO.get(disaster_type, DEFAULT_EVAC_RATIO)
        evacuees = int(round(covered_population * ratio))
        fallback = False
        if evacuees == 0:
            fallback = True
            evacuees = int(round(math.pi * radius_km * radius_km * FALLBACK_DENSITY))

        result = {
            "covered_population": covered_population,
            "evacuation_ratio": ratio,
            "estimated_evacuees": evacuees,
            "fallback_estimate": fallback,
            "townships": self.summarize_townships(covered),
            "villages": covered,
        }
        if total_remaining is not None:
            result["total_remaining"] = int(total_remaining)
            result["placeable"] = min(evacuees, int(total_remaining))
            result["shortfall"] = max(0, evacuees - int(total_remaining))
        return result
