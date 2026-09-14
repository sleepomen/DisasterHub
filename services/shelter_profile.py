import re

REGION_TAG_PATTERN = re.compile(r"^\[([A-Z]+)\]\s*")
REGION_LABELS = {"YILAN": "宜蘭", "HUALIEN": "花蓮", "TAITUNG": "台東"}
COUNTY_LABELS = {"YILAN": "宜蘭縣", "HUALIEN": "花蓮縣", "TAITUNG": "台東縣"}
TOWNSHIP_PATTERN = re.compile(r"(?:宜蘭縣|花蓮縣|臺東縣|台東縣)?([^\d\s]{1,3}?[市鄉鎮])")
STREET_PATTERN = re.compile(r"(?:[市鄉鎮])([^\d\s]{1,8}?(?:大路|路|街|大道)(?:[一二三四五六七八九十]段)?)")

FACILITY_RULES = [
    ("國小", "國小"),
    ("國中", "國中"),
    ("高中", "高中"),
    ("女中", "高中"),
    ("體育館", "體育場館"),
    ("體育場", "體育場館"),
    ("運動中心", "體育場館"),
    ("運動公園", "體育場館"),
    ("運動場", "體育場館"),
    ("圖書館", "圖書館"),
    ("公所", "公所"),
    ("會館", "會館"),
]

FACILITY_DESCRIPTIONS = {
    "國小": "國民小學，學校類型",
    "國中": "國民中學，學校類型",
    "高中": "高級中學，學校類型",
    "體育場館": "體育館或運動場，大型室內外運動場館",
    "圖書館": "公共圖書館",
    "公所": "鄉鎮市公所，地方行政機關",
    "會館": "文化會館，公共集會場所",
    "其他": "公共設施",
}

SIZE_CLASSES = [(300, "小型"), (800, "中型"), (float("inf"), "大型")]

LONG_FORMS = [
    ("國小附幼", "國民小學附設幼兒園"),
    ("國小", "國民小學"),
    ("國中", "國民中學"),
    ("女中", "女子高級中學"),
    ("高中", "高級中學"),
]

OWNER_PREFIX = re.compile(r"(縣立|市立|鎮立|鄉立)")
LEADING_PLACE = re.compile(r"^(宜蘭|花蓮|台東|臺東|羅東|蘇澳|頭城|礁溪|壯圍|冬山|五結|三星|大同|南澳)(縣|市|鎮|鄉)?")
GENERIC_ALIASES = {"體育館", "體育場", "圖書館", "公所", "高中", "女中", "國小", "國中", "運動中心", "運動場", "會館", "綜合運動場", "綜合體育場"}


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
    return m.group(1) if m else ""


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

    # 去掉地名後如果只剩「立體育館」這種所有權字尾，再把「立」拿掉；
    # 留著會變成好幾間共用的假別名，生成評測會把別間也算成有被提到
    stripped = LEADING_PLACE.sub("", clean)
    if stripped != clean:
        add(stripped.lstrip("立"))

    if "文化會館" in clean:
        add(clean.replace("文化會館", "會館"))
    if "原住民" in clean:
        add(clean.replace("原住民", "原民"))
    return out


def profile(name: str, address: str, capacity: int) -> dict:
    return {
        "region": region_of(name),
        "county": county_of(name),
        "township": township_of(address),
        "street": street_of(address),
        "road": road_of(address),
        "facility": facility_of(name),
        "size_class": size_class_of(capacity),
        "aliases": aliases_of(name),
    }
