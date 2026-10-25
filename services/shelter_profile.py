import re

REGION_TAG_PATTERN = re.compile(r"^\[([A-Z]+)\]\s*")
REGION_LABELS = {"YILAN": "宜蘭", "HUALIEN": "花蓮", "TAITUNG": "台東"}
COUNTY_LABELS = {"YILAN": "宜蘭縣", "HUALIEN": "花蓮縣", "TAITUNG": "台東縣"}
TOWNSHIP_PATTERN = re.compile(r"(?:宜蘭縣|花蓮縣|臺東縣|台東縣)?([^\d\s]{1,3}?[市鄉鎮])")
STREET_PATTERN = re.compile(r"(?:[市鄉鎮])([^\d\s]{1,8}?(?:大路|路|街|大道)(?:[一二三四五六七八九十]段)?)")

# 避難所名稱 → facility 標籤（順序代表優先權，先命中先贏）。
# 這是「名稱 → 標籤」；query_rules 那張是「問句 → 標籤」，兩者方向不同不能合併，
# 但標籤詞彙必須一致，所以由下面的 FACILITY_LABELS 統一
FACILITY_RULES = [
    ("國小", "國小"),
    ("國民小學", "國小"),
    ("國中", "國中"),
    ("國民中學", "國中"),
    ("高中", "高中"),
    ("高級中學", "高中"),
    ("女中", "高中"),
    ("高商", "高中"),
    ("高工", "高中"),
    ("職業學校", "高中"),
    ("海事", "高中"),
    ("大學", "大學"),
    ("附幼", "幼兒園"),
    ("幼兒園", "幼兒園"),
    ("托兒所", "幼兒園"),
    ("體育館", "體育場館"),
    ("體育場", "體育場館"),
    ("運動中心", "體育場館"),
    ("運動公園", "體育場館"),
    ("運動場", "體育場館"),
    ("棒球場", "體育場館"),
    ("圖書館", "圖書館"),
    ("公所", "公所"),
    ("辦公處", "公所"),
    ("辦公室", "公所"),
    # 消防署資料一半以上是社區 / 村里活動中心；「老人活動中心」「文康中心」也是活動中心，所以排在社福前面
    ("活動中心", "活動中心"),
    ("文康中心", "活動中心"),
    ("集會所", "活動中心"),
    ("會館", "會館"),
    ("文化館", "會館"),
    ("教會", "宗教場所"),
    ("聚會所", "宗教場所"),
    ("天主堂", "宗教場所"),
    ("慈惠堂", "宗教場所"),
    ("宮", "宗教場所"),
    ("寺", "宗教場所"),
    ("廟", "宗教場所"),
    ("育幼院", "社福機構"),
    ("老人", "社福機構"),
    ("之家", "社福機構"),
    ("社福", "社福機構"),
    ("社會福利", "社福機構"),
    ("福利中心", "社福機構"),
    ("營區", "軍事營區"),
    ("酒店", "旅館"),
    ("飯店", "旅館"),
    ("渡假村", "旅館"),
    ("Hotel", "旅館"),
]

# metadata 的 facility 標籤詞彙。這裡是唯一的來源：從 FACILITY_RULES 推導而不是手寫第三份清單。
# 索引端（這個模組）決定寫進 metadata 的值，查詢端（query_rules）只能用這組標籤——
# 用了不存在的標籤，ChromaDB 的 where 篩選會永遠篩不到東西，而且會安靜地退回一般檢索
FACILITY_LABELS = frozenset({label for _, label in FACILITY_RULES} | {"其他"})

# 消防署資料的收容人數中位數只有 70 人（舊的手動資料是幾百上千）：分界取在 50 / 150，
# 三級大約各占 40% / 41% / 19%。收容人數填 0 的是來源沒提供，不能算成小型
SIZE_CLASSES = [(50, "小型"), (150, "中型"), (float("inf"), "大型")]
UNKNOWN_SIZE = "未提供"

# 消防署「適用災害類別」→ metadata 的布林欄位。ChromaDB 的 metadata 不收 list，只能一類一欄
HAZARD_KEYS = {"水災": "hz_flood", "震災": "hz_quake", "土石流": "hz_debris", "海嘯": "hz_tsunami"}

LONG_FORMS = [
    ("國小附幼", "國民小學附設幼兒園"),
    ("國小", "國民小學"),
    ("國中", "國民中學"),
    ("女中", "女子高級中學"),
    ("高中", "高級中學"),
]

# 消防署資料多半用全名（「關山國民小學」「蘭嶼完全高級中學」），使用者說的是簡稱：反過來也要有別名
SHORT_FORMS = [
    ("完全高級中學", "高中"),
    ("高級中學", "高中"),
    ("國民小學", "國小"),
    ("國民中學", "國中"),
]

OWNER_PREFIX = re.compile(r"(縣立|市立|鎮立|鄉立|國立|私立)")
# 三縣全部 41 個鄉鎮市。以前取自人口模型的 29 個，消防署資料一進來就多了蘭嶼、綠島、卓溪、海端這些，
# 「卓溪鄉的避難所」會篩不到鄉鎮、退回一般檢索。行政區很少變動，直接列出來
TOWNSHIPS = {
    "宜蘭": ["宜蘭市", "羅東鎮", "蘇澳鎮", "頭城鎮", "礁溪鄉", "壯圍鄉", "員山鄉", "冬山鄉", "五結鄉",
             "三星鄉", "大同鄉", "南澳鄉"],
    "花蓮": ["花蓮市", "鳳林鎮", "玉里鎮", "新城鄉", "吉安鄉", "壽豐鄉", "光復鄉", "豐濱鄉", "瑞穗鄉",
             "富里鄉", "秀林鄉", "萬榮鄉", "卓溪鄉"],
    "台東": ["台東市", "成功鎮", "關山鎮", "卑南鄉", "大武鄉", "太麻里鄉", "東河鄉", "長濱鄉", "鹿野鄉",
             "池上鄉", "綠島鄉", "延平鄉", "海端鄉", "達仁鄉", "金峰鄉", "蘭嶼鄉"],
}

# 名稱開頭的縣名或鄉鎮名（「花蓮縣鶴岡國民小學」「關山鎮關山國民小學」）去掉之後才是使用者會講的名字。
# 以前只列宜蘭的鄉鎮：舊資料只有宜蘭的名稱這樣寫
_PLACE_STEMS = sorted({"宜蘭", "花蓮", "台東", "臺東"} | {town[:-1] for towns in TOWNSHIPS.values() for town in towns},
                      key=len, reverse=True)
LEADING_PLACE = re.compile(rf"^({'|'.join(_PLACE_STEMS)})(縣|市|鎮|鄉)?")
GENERIC_ALIASES = {
    "體育館", "體育場", "圖書館", "公所", "高中", "女中",
    "國小", "國中", "運動中心", "運動場", "會館", "綜合運動場", "綜合體育場",
}


# 去掉開頭地名之後如果剩下的是這些開頭，代表地名就是名字本身，不產生別名
GENERIC_STARTS = tuple(kw for kw, _ in FACILITY_RULES) + ("社區", "村", "里", "多功能", "聯合", "綜合")


def region_code(tagged_name: str) -> str:
    m = REGION_TAG_PATTERN.match(tagged_name or "")
    return m.group(1) if m else ""


def region_of(tagged_name: str) -> str:
    return REGION_LABELS.get(region_code(tagged_name), "")


def county_of(tagged_name: str) -> str:
    return COUNTY_LABELS.get(region_code(tagged_name), "")


def strip_region_tag(tagged_name: str) -> str:
    return REGION_TAG_PATTERN.sub("", tagged_name or "")


def township_of(address: str) -> str:
    m = TOWNSHIP_PATTERN.search(address or "")
    return normalize_place(m.group(1)) if m else ""


def normalize_place(name: str) -> str:
    """臺 / 台 統一成「台」：消防署資料寫臺東市，人口模型與查詢規則一律用台東市"""
    return (name or "").replace("臺", "台")


def street_of(address: str) -> str:
    m = STREET_PATTERN.search(address or "")
    return m.group(1) if m else ""


SECTION_SUFFIX = re.compile(r"[一二三四五六七八九十]段$")


def road_of(address: str) -> str:
    """路名去掉「段」：使用者問「四維路」時，四維路一段與二段的避難所都要對得上"""
    return SECTION_SUFFIX.sub("", street_of(address))


def facility_of(name: str) -> str:
    clean = strip_region_tag(name)
    return next((label for kw, label in FACILITY_RULES if kw in clean), "其他")


def size_class_of(capacity: int) -> str:
    if capacity <= 0:
        return UNKNOWN_SIZE
    for limit, label in SIZE_CLASSES:
        if capacity <= limit:
            return label
    return "大型"


def aliases_of(name: str) -> list[str]:
    clean = strip_region_tag(name)
    out = []

    def add(candidate: str):
        candidate = candidate.strip()
        if (
            candidate
            and candidate != clean
            and candidate not in out
            and len(candidate) >= 3
            and candidate not in GENERIC_ALIASES
        ):
            out.append(candidate)

    for short, long in LONG_FORMS:
        if short in clean:
            add(clean.replace(short, long))
            break

    without_owner = OWNER_PREFIX.sub("", clean)
    add(without_owner)

    for long, short in SHORT_FORMS:
        if long in clean:
            add(clean.replace(long, short))
            add(without_owner.replace(long, short))
            break

    # 去掉地名後如果只剩「立體育館」這種所有權字尾，再把「立」拿掉；
    # 留著會變成好幾間共用的假別名，生成評測會把別間也算成有被提到
    stripped = LEADING_PLACE.sub("", clean).lstrip("立")
    # 地名本身就是名字的一部分時不能拿掉：「成功國小體育館」去掉成功剩「國小體育館」、「大同活動中心」剩「活動中心」，
    # 這種通用名稱當別名會讓錯字比對同時像好幾間，結果一間都改不了
    if stripped != clean and not stripped.startswith(GENERIC_STARTS):
        add(stripped)
        for long, short in SHORT_FORMS:
            if long in stripped:
                add(stripped.replace(long, short))
                break

    if "文化會館" in clean:
        add(clean.replace("文化會館", "會館"))
    if "原住民" in clean:
        add(clean.replace("原住民", "原民"))
    return out


def details_text(s) -> str:
    """
    消防署點位檔帶來的細節 → 文件裡的一段話。沒填的欄位整句省略，不寫「未知」：
    模型會把「適合避難弱者：未知」講成「不適合」
    """
    parts = []
    hazards = list(getattr(s, "hazards", ()) or ())
    if hazards:
        parts.append(f"適用災害：{'、'.join(hazards)}。")
    indoor, outdoor = getattr(s, "indoor", None), getattr(s, "outdoor", None)
    if indoor and outdoor:
        parts.append("有室內與室外場地。")
    elif indoor:
        parts.append("室內場地。")
    elif outdoor:
        parts.append("室外場地。")
    vulnerable = getattr(s, "vulnerable_ok", None)
    if vulnerable is True:
        parts.append("適合安置避難弱者（長者、身障者等）。")
    elif vulnerable is False:
        parts.append("未列為適合安置避難弱者。")
    serves = getattr(s, "serves", "")
    if serves:
        parts.append(f"預計收容：{serves}。")
    phone = getattr(s, "phone", "")
    if phone:
        parts.append(f"聯絡電話：{phone}。")
    return "".join(parts)


def profile(name: str, address: str, capacity: int, township: str = "") -> dict:
    """township 是資料自帶的鄉鎮（消防署欄位）；沒有時才從地址解析——地址可能寫的是別的鄉鎮"""
    return {
        "region": region_of(name),
        "county": county_of(name),
        "township": normalize_place(township) or township_of(address),
        "street": street_of(address),
        "road": road_of(address),
        "facility": facility_of(name),
        "size_class": size_class_of(capacity),
        "aliases": aliases_of(name),
    }
