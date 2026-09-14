"""
查詢規則層：在向量檢索之前先讀懂問題裡的「硬條件」。

向量距離擋不住「高雄的避難所」這種範圍外問題，也裝不下「宜蘭有哪些避難所」要的 20 筆結果，
所以先用規則抽出地區 / 鄉鎮 / 設施類型 / 容量條件，轉成 ChromaDB 的 metadata 篩選；
範圍外的縣市直接拒答，不進向量檢索。
"""
import re
from dataclasses import dataclass, field

from services.population_service import PopulationModel

# 系統涵蓋的三個縣，對應 metadata 的 region 欄位
REGION_ALIASES = {
    "宜蘭": "宜蘭",
    "花蓮": "花蓮",
    "台東": "台東",
    "臺東": "台東",
}

# 台灣其他縣市：出現就視為範圍外，除非同句也提到範圍內的地區或鄉鎮
OUT_OF_SCOPE_PLACES = [
    "基隆", "台北", "臺北", "新北", "桃園", "新竹", "苗栗", "台中", "臺中",
    "彰化", "南投", "雲林", "嘉義", "台南", "臺南", "高雄", "屏東",
    "澎湖", "金門", "馬祖", "連江",
]

# 設施關鍵字 → metadata 的 facility 標籤（順序代表優先權，先命中先贏）
FACILITY_KEYWORDS = [
    ("國小", ["國小"]),
    ("小學", ["國小"]),
    ("國中", ["國中"]),
    ("高中", ["高中"]),
    ("女中", ["高中"]),
    ("學校", ["國小", "國中", "高中"]),
    ("體育館", ["體育場館"]),
    ("體育場", ["體育場館"]),
    ("運動中心", ["體育場館"]),
    ("運動公園", ["體育場館"]),
    ("運動場", ["體育場館"]),
    ("圖書館", ["圖書館"]),
    ("公所", ["公所"]),
    ("市政府", ["公所"]),
    ("縣政府", ["公所"]),
    ("會館", ["會館"]),
    ("文化館", ["會館"]),
]

SIZE_CLASS_KEYWORDS = ["大型", "中型", "小型"]

# 容量條件
CAP_THOUSAND = re.compile(r"上千|千人|一千|1000\s*人")
CAP_MORE_THAN = re.compile(r"(?:超過|多於|大於)\s*(\d{2,6})")
CAP_AT_LEAST = re.compile(r"(\d{2,6})\s*人?\s*(?:以上|起)|至少\s*(\d{2,6})")
CAP_AT_MOST = re.compile(r"(\d{2,6})\s*人?\s*(?:以下|以內|內)|(?:不到|少於|低於|小於)\s*(\d{2,6})")
SUPERLATIVE = re.compile(r"最大|最多|容納最多|收最多|最能收|最寬敞|容量最高|容量排名|容量排序|由大到小|最小|容量最低|由小到大")
ASCENDING = re.compile(r"最小|容量最低|由小到大")

# 路名：「四維路」「中華路一段附近」「桂林北路的避難所」。向量距離對純路名很不敏感（同一條路的兩間學校
# 在語意上毫無關係），所以抽出來做 metadata 精確篩選。字元類別排除行政區與助詞，「宜蘭市中山路」才會抽到「中山路」；
# 後面必須接段 / 巷 / 號 / 助詞 / 句尾，「避難所路線」這種才不會被抽成「避難所路」
ROAD_PATTERN = re.compile(
    r"(?:(?![市鄉鎮縣的在有到去往])[一-鿿]){1,6}?(?:大路|大道|路|街)"
    r"(?=$|[一二三四五六七八九十]段|巷|號|上|的|附近|一帶|旁|邊|口|周|那|這|有|哪|[\s，,。？?、])"
)
ROAD_BLOCKLIST = {"走路", "網路", "道路", "公路", "馬路", "逛街", "上街", "大路", "大道", "一路"}

# 鄉鎮字尾；去掉字尾的「詞幹」也允許命中（「礁溪」→ 礁溪鄉），但這幾個詞幹太像一般用語，不做詞幹比對
TOWNSHIP_SUFFIXES = ("市", "鄉", "鎮")
STEM_BLOCKLIST = {"成功", "大同", "光復", "新城"}

# 不做篩選時的一般檢索筆數上限；有篩選時最多回傳幾筆（涵蓋單一縣的全部避難所）
MAX_FILTERED_RESULTS = 30
# 「最大 / 排名」類問題依容量排序後最多列幾筆，太多反而讓模型抓不到重點
MAX_RANKED_RESULTS = 10

_township_cache: dict[str, str] | None = None


def known_townships() -> dict[str, str]:
    """鄉鎮全名 → 所屬地區（宜蘭 / 花蓮 / 台東），來自人口模型；載入失敗就是空表"""
    global _township_cache
    if _township_cache is None:
        model = PopulationModel()
        county_to_region = {"宜蘭縣": "宜蘭", "花蓮縣": "花蓮", "台東縣": "台東", "臺東縣": "台東"}
        _township_cache = {
            t["name"]: county_to_region.get(t["county"], "")
            for t in model.townships
            if t["name"].endswith(TOWNSHIP_SUFFIXES)
        }
    return _township_cache


@dataclass
class QueryPlan:
    region: str | None = None
    township: str | None = None
    road: str | None = None
    facilities: list[str] = field(default_factory=list)
    capacity_min: int | None = None
    capacity_max: int | None = None
    size_class: str | None = None
    order_by_capacity: bool = False
    capacity_order: str = "desc"  # desc = 由大到小；asc = 由小到大
    out_of_scope: str | None = None  # 命中的範圍外地名

    @property
    def has_filter(self) -> bool:
        return any([
            self.region, self.township, self.road, self.facilities,
            self.capacity_min is not None, self.capacity_max is not None, self.size_class,
        ])

    def to_where(self) -> dict | None:
        """轉成 ChromaDB where 子句；沒有條件回 None"""
        clauses = []
        if self.township:
            clauses.append({"township": self.township})
        elif self.region:
            clauses.append({"region": self.region})
        if self.road:
            clauses.append({"road": self.road})
        if self.facilities:
            if len(self.facilities) == 1:
                clauses.append({"facility": self.facilities[0]})
            else:
                clauses.append({"facility": {"$in": list(self.facilities)}})
        if self.capacity_min is not None:
            clauses.append({"capacity": {"$gte": self.capacity_min}})
        if self.capacity_max is not None:
            clauses.append({"capacity": {"$lte": self.capacity_max}})
        if self.size_class and self.capacity_min is None and self.capacity_max is None:
            clauses.append({"size_class": self.size_class})
        if not clauses:
            return None
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    def describe(self) -> str:
        parts = []
        if self.township:
            parts.append(self.township)
        elif self.region:
            parts.append(f"{self.region}地區")
        if self.road:
            parts.append(self.road)
        if self.facilities:
            parts.append("/".join(self.facilities))
        if self.capacity_min is not None:
            parts.append(f"容量 ≥ {self.capacity_min}")
        if self.capacity_max is not None:
            parts.append(f"容量 ≤ {self.capacity_max}")
        elif self.size_class:
            parts.append(self.size_class)
        return "、".join(parts)


def _find_region(query: str) -> str | None:
    for alias, region in REGION_ALIASES.items():
        if alias in query:
            return region
    return None


def _find_township(query: str) -> tuple[str | None, str | None]:
    """回傳 (鄉鎮全名, 所屬地區)。全名優先；詞幹只在不是縣名時才算"""
    towns = known_townships()
    # 全名：最長的先比，避免「台東市」被「台東」搶走
    for name in sorted(towns, key=len, reverse=True):
        if name in query:
            return name, towns[name]
    for name in sorted(towns, key=len, reverse=True):
        stem = name[:-1]
        if len(stem) < 2 or stem in STEM_BLOCKLIST or stem in REGION_ALIASES:
            continue
        if stem in query:
            return name, towns[name]
    return None, None


def _find_road(query: str) -> str | None:
    for m in ROAD_PATTERN.finditer(query):
        road = m.group(0)
        if road not in ROAD_BLOCKLIST:
            return road
    return None


def _find_facilities(query: str) -> list[str]:
    for keyword, labels in FACILITY_KEYWORDS:
        if keyword in query:
            return list(labels)
    return []


def _first_int(match: re.Match | None) -> int | None:
    if not match:
        return None
    for g in match.groups():
        if g:
            return int(g)
    return None


def _find_capacity(query: str) -> tuple[int | None, int | None]:
    cap_min = None
    cap_max = None
    if CAP_THOUSAND.search(query):
        cap_min = 1000
    more_than = _first_int(CAP_MORE_THAN.search(query))
    if more_than is not None:
        cap_min = max(cap_min or 0, more_than + 1)
    at_least = _first_int(CAP_AT_LEAST.search(query))
    if at_least is not None:
        cap_min = max(cap_min or 0, at_least)
    at_most = _first_int(CAP_AT_MOST.search(query))
    if at_most is not None:
        cap_max = at_most
    return cap_min, cap_max


def analyze(query: str) -> QueryPlan:
    query = query or ""
    plan = QueryPlan()

    plan.region = _find_region(query)
    plan.township, township_region = _find_township(query)
    if plan.township and not plan.region:
        plan.region = township_region or None

    # 範圍外地名：只有在整句沒有任何範圍內線索時才拒答
    if not plan.region and not plan.township:
        for place in OUT_OF_SCOPE_PLACES:
            if place in query:
                plan.out_of_scope = place
                return plan

    plan.road = _find_road(query)
    plan.facilities = _find_facilities(query)
    plan.capacity_min, plan.capacity_max = _find_capacity(query)
    plan.size_class = next((s for s in SIZE_CLASS_KEYWORDS if s in query), None)
    plan.order_by_capacity = bool(SUPERLATIVE.search(query))
    plan.capacity_order = "asc" if ASCENDING.search(query) else "desc"
    return plan


def out_of_scope_reply(place: str) -> str:
    return (
        f"本系統只涵蓋宜蘭、花蓮、台東三縣的避難所資料，目前沒有{place}的避難所資訊。"
        "請改問東部地區的避難所，或洽當地政府的災害應變中心。"
    )
