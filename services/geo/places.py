"""
小地名 → 座標：「知本附近有避難所嗎」「我在七星潭」「三仙台最近的避難所」。

這些地名不是鄉鎮，規則層不認得，以前只能靠語意檢索猜，2026-11-02 的小地名評測
（evals/run_place_eval.py）量到前 5 間只有 31% 落在對的鄉鎮：「七星潭」撈到整排宜蘭、
「南方澳」因為字像撈到南澳鄉。換成座標之後就跟給座標問最近避難所一樣，依道路距離由程式回答。

地名有兩個來源：
- data_reference/east_places.json：OpenStreetMap 的村落、部落、社區、海灣、湖、山、景點（tools/build_place_names.py）
- 避難所資料的村里（「知本里」→「知本」），補 OSM 沒有的；座標用該村里避難所的平均位置

只在「地名＋附近／最近／周邊…」或「我在／住在＋地名」的句型裡認地名：兩個字的地名很多是一般詞
（和平、復興、平台），整句隨便比對會誤判。同名的地方 5 公里內算同一處；分散成 4 處以上的
（觀景台、生態池、清水）當成一般詞，不認
"""
import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path

from services.retrieval.shelter_profile import TOWNSHIPS

logger = logging.getLogger(__name__)

PLACES_FILE = Path(__file__).resolve().parents[2] / "data_reference" / "east_places.json"

SAME_PLACE_KM = 5.0
MAX_PLACES_PER_NAME = 3
AFTER_PLACE = ("附近", "周邊", "周圍", "一帶", "最近", "旁邊", "這邊", "那邊", "的避難所", "有避難所", "有什麼避難所",
               "有哪些避難所", "可以去哪", "要去哪", "該去哪")
BEFORE_PLACE = ("我在", "人在", "住在", "我人在", "目前在", "現在在", "我們在")
REGION_OF_TAG = {"YILAN": "宜蘭縣", "HUALIEN": "花蓮縣", "TAITUNG": "台東縣"}
# 鄉鎮全名、簡稱、縣名：規則層已經認得，不當小地名
TOWNSHIP_NAMES = (
    {t for ts in TOWNSHIPS.values() for t in ts}
    | {t[:-1] for ts in TOWNSHIPS.values() for t in ts if len(t) > 2}
    | {"宜蘭", "花蓮", "台東", "臺東", "宜蘭縣", "花蓮縣", "台東縣", "臺東縣"}
)


@dataclass
class Place:
    name: str
    lat: float
    lon: float
    # 最靠近的避難所所在的縣與鄉鎮，同名的地方要分得出來（「壯圍鄉的新社」「豐濱鄉的新社」）
    county: str = ""
    township: str = ""

    @property
    def area(self) -> str:
        return f"{self.county}{self.township}"


def km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _cluster(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """5 公里內的點算同一處，回傳每一處的中心"""
    groups: list[list[tuple[float, float]]] = []
    for lat, lon in points:
        for g in groups:
            if km(lat, lon, *g[0]) <= SAME_PLACE_KM:
                g.append((lat, lon))
                break
        else:
            groups.append([(lat, lon)])
    return [(sum(p[0] for p in g) / len(g), sum(p[1] for p in g) / len(g)) for g in groups]


class PlaceIndex:
    def __init__(self, osm_places: list[dict], shelters: list | None = None):
        points: dict[str, list[tuple[float, float]]] = {}
        for p in osm_places:
            for form in p["forms"]:
                if form not in TOWNSHIP_NAMES:
                    points.setdefault(form, []).append((p["lat"], p["lon"]))
        shelters = shelters or []
        for stem, coords in self._villages(shelters).items():
            # OSM 已經有、而且在附近的就不重複；不在附近的是另一個同名的地方
            points.setdefault(stem, []).extend(coords)
        self.shelters = shelters
        self.places: dict[str, list[Place]] = {}
        for form, pts in points.items():
            centers = _cluster(pts)
            if len(centers) <= MAX_PLACES_PER_NAME:
                self.places[form] = [self._locate(form, lat, lon) for lat, lon in centers]
        # 長的先比：「七星潭」不能被「七星」搶走
        self.forms = sorted(self.places, key=len, reverse=True)

    @staticmethod
    def _villages(shelters) -> dict[str, list[tuple[float, float]]]:
        """避難所資料的村里：「知本里」→「知本」，座標是那個村里每間避難所的平均（一個村里算一個點）"""
        by_village: dict[tuple[str, str], list[tuple[float, float]]] = {}
        for s in shelters:
            village = getattr(s, "village", "") or ""
            if village[-1:] in ("村", "里") and len(village) >= 3:
                by_village.setdefault((s.township, village[:-1]), []).append((s.lat, s.lon))
        stems: dict[str, list[tuple[float, float]]] = {}
        for (_, stem), coords in by_village.items():
            if stem not in TOWNSHIP_NAMES:
                lat = sum(c[0] for c in coords) / len(coords)
                lon = sum(c[1] for c in coords) / len(coords)
                stems.setdefault(stem, []).append((lat, lon))
        return stems

    def _locate(self, name: str, lat: float, lon: float) -> Place:
        place = Place(name, round(lat, 5), round(lon, 5))
        if self.shelters:
            nearest = min(self.shelters, key=lambda s: km(lat, lon, s.lat, s.lon))
            tag = nearest.name[1:nearest.name.find("]")] if nearest.name.startswith("[") else ""
            place.county = REGION_OF_TAG.get(tag, "")
            place.township = nearest.township or ""
        return place

    def find(self, question: str) -> tuple[str, list[Place]] | None:
        """
        問句裡在「地名＋附近」「我在＋地名」句型中的小地名 → (地名, 同名的幾處)；沒有回 None。
        問句同時寫了縣或鄉鎮（「豐濱新社附近」）時，只留那一處
        """
        for form in self.forms:
            start = question.find(form)
            while start != -1:
                before, after = question[:start], question[start + len(form):]
                if after.startswith(AFTER_PLACE) or before.endswith(BEFORE_PLACE):
                    return form, self._narrow(question, self.places[form])
                start = question.find(form, start + 1)
        return None

    @staticmethod
    def _narrow(question: str, places: list[Place]) -> list[Place]:
        normalized = question.replace("臺", "台")
        picked = [
            p for p in places
            if (p.township and (p.township.replace("臺", "台") in normalized or p.township[:-1].replace("臺", "台") in normalized))
            or (p.county and p.county[:2] in normalized)
        ]
        return picked or places


def load_osm_places(path: Path = PLACES_FILE) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))["places"]
    except Exception:
        # 地名檔是附加資訊：讀不到就只剩避難所資料的村里，不讓聊天跟著壞
        logger.exception("讀取地名檔 %s 失敗", path)
        return []


def place_label(place: Place, total: int) -> str:
    """同名的地方要加上縣與鄉鎮才分得出來"""
    return f"{place.area}的{place.name}" if total > 1 and place.area else place.name
