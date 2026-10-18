"""
查詢規則層：在向量檢索之前先讀懂問題裡的「硬條件」。

向量距離擋不住「高雄的避難所」這種範圍外問題，也裝不下「宜蘭有哪些避難所」要的 20 筆結果，
所以先用規則抽出地區 / 鄉鎮 / 設施類型 / 容量條件，轉成 ChromaDB 的 metadata 篩選；
範圍外的縣市直接拒答，不進向量檢索。
"""
import re
from dataclasses import dataclass, field

from services.population_service import PopulationModel
from services.shelter_profile import FACILITY_LABELS

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

# 查詢端用的標籤必須真的存在於索引端的詞彙裡。少了這道檢查，在這裡加一個
# shelter_profile 沒有的標籤不會報錯：metadata 篩選只會永遠落空，然後退回一般檢索
# （prompt 的標頭會說「沒有找到符合條件的」），看起來像檢索品質差而不是設定錯誤
_UNKNOWN_FACILITIES = {label for _, labels in FACILITY_KEYWORDS for label in labels} - FACILITY_LABELS
if _UNKNOWN_FACILITIES:
    raise RuntimeError(
        f"FACILITY_KEYWORDS 用了 shelter_profile.FACILITY_LABELS 沒有的設施標籤：{sorted(_UNKNOWN_FACILITIES)}"
    )

# 明顯不是避難所的主題。邊界題評測發現：問句只要帶到範圍內地名就會產生 metadata 篩選，
# 而距離門檻在有篩選的路徑上依設計被跳過，於是「宜蘭的超商在哪」會拿到 20 筆避難所——
# 而且實測 LLM 這條最後防線只攔住 13 題裡的 4 題。
#
# 這是黑名單，所以失效方向是安全的：沒列到的詞落到 LLM，也就是維持原本的行為，
# 而不是誤擋正當問題。反過來的做法（要求問句必須提到避難主題）實測會誤擋 120 道正例裡的
# 16 題——「礁溪」「冬山」「壯圍有哪些」這種只有地名的簡短問句，在避難所系統裡顯然是在問避難所。
OFF_TOPIC_WORDS = [
    "天氣", "下雨", "氣溫", "溫度", "降雨",
    "補助", "紓困", "津貼", "理賠", "保險",
    "醫院", "診所", "藥局", "加油站", "超商", "便利商店", "停車場", "郵局", "銀行",
    "餐廳", "飯店", "民宿", "公車", "火車", "機場",
]

# 同一句話同時提到避難主題時就不算離題：「下雨天要去哪個避難所」問的是避難所，
# 「淹水時收容所在哪」也是。少了這個條件，這類正當問題會被誤擋
SHELTER_TOPIC_WORDS = ["避難", "收容", "安置", "疏散", "空位", "空間", "容量"]

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
    off_topic: str | None = None  # 命中的非避難所主題（天氣、醫院、補助…）

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

    # 離題判定放在範圍外之後：「高雄的天氣」兩者都中，回報縣市涵蓋範圍比較精確
    plan.off_topic = _find_off_topic(query)
    if plan.off_topic:
        return plan

    plan.road = _find_road(query)
    plan.facilities = _find_facilities(query)
    plan.capacity_min, plan.capacity_max = _find_capacity(query)
    plan.size_class = next((s for s in SIZE_CLASS_KEYWORDS if s in query), None)
    plan.order_by_capacity = bool(SUPERLATIVE.search(query))
    plan.capacity_order = "asc" if ASCENDING.search(query) else "desc"
    return plan


def _find_off_topic(query: str) -> str | None:
    """問句點名了非避難所的主題，而且整句沒有提到避難主題時，回報命中的那個詞"""
    if any(word in query for word in SHELTER_TOPIC_WORDS):
        return None
    return next((word for word in OFF_TOPIC_WORDS if word in query), None)


def off_topic_reply(topic: str) -> str:
    # 開頭刻意跟 out_of_scope_reply 用同一句「本系統只涵蓋」：
    # evals/gen_metrics.py 的 ABSTAIN_MARKERS 認的是這個字串，
    # 換掉的話生成評測的 abstain_correct 會無聲下降（tests 有釘住）
    return (
        f"本系統只涵蓋宜蘭、花蓮、台東三縣的避難收容場所資訊，沒有{topic}的資料。"
        "可以問避難所的位置、容量、剩餘空位，或最近的避難所在哪裡。"
    )


def out_of_scope_reply(place: str) -> str:
    return (
        f"本系統只涵蓋宜蘭、花蓮、台東三縣的避難所資料，目前沒有{place}的避難所資訊。"
        "請改問東部地區的避難所，或洽當地政府的災害應變中心。"
    )


# 問句點名了一個「看起來是場所」的名稱：字首 2～8 個字＋設施字尾。字首排除助詞與口語詞，
# 「請問頭城國中」才會抽到「頭城國中」，「宜蘭的國小」「有哪些國中」則抽不到任何名稱。
# 字尾也收資料裡沒有的類型（游泳池、美術館…）：問到這些一定是不存在的場所
NAMED_FACILITY_SUFFIXES = sorted([
    "國民小學", "國民中學", "高級中學", "綜合運動場", "綜合體育場", "運動中心", "活動中心", "文化會館",
    "國小", "國中", "高中", "女中", "小學", "體育館", "體育場", "運動場", "圖書館", "公所", "會館",
    "游泳池", "美術館", "博物館", "文化中心",
], key=len, reverse=True)
_PREFIX_CHAR = r"(?:(?![的有是在與和或及個間位該這那此各等某其哪些什麼幾最附近我你要想問請找去到往從離跟靠])[一-鿿])"
NAMED_FACILITY_RE = re.compile(rf"({_PREFIX_CHAR}{{2,8}})({'|'.join(NAMED_FACILITY_SUFFIXES)})")

# 字首是這些時是在問「一類」場所，不是點名某一間：「大型體育館」「東部國小」
GENERIC_PREFIXES = {"大型", "中型", "小型", "東部", "所有", "全部", "其他", "一般", "任何", "公立", "縣立", "市立", "鄉立", "鎮立"}
COUNTY_NAMES = {"宜蘭縣", "花蓮縣", "台東縣", "臺東縣"}


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _is_subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(ch in it for ch in short)


def _is_place(prefix: str, stems_too: bool) -> bool:
    if prefix in REGION_ALIASES or prefix in COUNTY_NAMES or prefix in known_townships():
        return True
    return stems_too and any(prefix == name[:-1] for name in known_townships())


@dataclass
class NamedFacility:
    """問句點名的場所跟避難所名單比對的結果"""
    fragment: str             # 問句裡的寫法
    typo_of: str | None = None  # 錯一個字時，對應到的真實名稱（或別名）；None 代表資料裡沒有這間


def match_named_facility(query: str, known_names: list[str]) -> NamedFacility | None:
    """
    問句點名了名單裡沒有的場所時回傳結果；名稱存在、問的是一類、或抽不到名稱時回 None。

    邊界題評測（llama3.2:3b）：問「頭城國中」「卑南國小」這種不存在、但跟真實避難所只差一兩個字的名稱，
    12 題只有 2～4 題會交代「資料裡沒有」，其餘不是替它編出容量與地址，就是直接拿頭城國小來答。
    篩選落空時 prompt 標頭明寫「請先告知使用者沒有完全符合條件的避難所」，那 6 題一題都沒照做，
    所以這件事不能交給模型判斷，由程式先攔下。

    錯字與「不同場所」要分開：
      - 字尾不是合法的設施類型（中正國少、體育官）→ 抽不到名稱，照常走檢索
      - 字尾相同、字首只差一個字，而且只對得到一間（涙強國小 vs 濤強國小）→ 錯字，typo_of 指向那間
      - 但字首本身是地名時（羅東高中 vs 台東高中）就是另一個地方，不是錯字
      - 字尾不同（新生高中 vs 新生國中）→ 不同的學校
    known_names 為空（索引還沒建好）時不判斷，免得把每個名稱都說成不存在。
    """
    if not known_names:
        return None
    if any(name in query for name in known_names):
        return None
    for m in NAMED_FACILITY_RE.finditer(query):
        prefix, suffix = m.group(1), m.group(2)
        fragment = prefix + suffix
        if prefix in GENERIC_PREFIXES or _is_place(prefix, stems_too=False):
            continue
        # 「中興國小」是「中興國小附幼」的前半，指的是同一間
        if any(fragment in name for name in known_names):
            continue
        same_suffix = [name for name in known_names if name.endswith(suffix)]
        # 簡稱：「原民會館」的字依序都在「原住民文化會館」裡
        if any(_is_subsequence(prefix, name[: -len(suffix)]) for name in same_suffix):
            continue
        # 字首是地名就是另一個地方，不是錯字：「羅東高中」不是「台東高中」，
        # 「台中體育館」「屏東縣立體育館」更不能被改成台東的那間——它們該由範圍外規則拒答
        other_place = _is_place(prefix, stems_too=True) or prefix.startswith(tuple(OUT_OF_SCOPE_PLACES))
        if not other_place:
            close =[name for name in same_suffix if _edit_distance(prefix, name[: -len(suffix)]) <= 1]
            if len(close) == 1:
                return NamedFacility(fragment, typo_of=close[0])
            if close:
                # 同時像好幾間：猜哪一間都可能猜錯，交給檢索
                continue
        return NamedFacility(fragment)
    return None


def unknown_shelter_name(query: str, known_names: list[str]) -> str | None:
    """問句點名的場所不在資料裡（而且不是錯字）時回傳那個名稱"""
    found = match_named_facility(query, known_names)
    return found.fragment if found and found.typo_of is None else None


def typo_correction(query: str, known_names: list[str]) -> tuple[str, str] | None:
    """問句點名的場所跟某間避難所只差一個字時，回傳 (問句裡的寫法, 真實名稱)"""
    found = match_named_facility(query, known_names)
    return (found.fragment, found.typo_of) if found and found.typo_of else None
