import pytest

from services.retrieval.shelter_profile import (
    aliases_of,
    county_of,
    details_text,
    facility_of,
    profile,
    region_of,
    size_class_of,
    street_of,
    strip_region_tag,
    township_of,
)


def test_region_and_county():
    assert region_of("[HUALIEN] 花蓮縣立體育館") == "花蓮"
    assert county_of("[TAITUNG] 台東女中") == "台東縣"
    assert region_of("沒有標籤") == ""
    assert strip_region_tag("[YILAN] 宜蘭國小") == "宜蘭國小"


@pytest.mark.parametrize("address,township", [
    ("花蓮縣花蓮市達固湖灣大路23號", "花蓮市"),
    ("宜蘭縣礁溪鄉礁溪路四段23號", "礁溪鄉"),
    ("台東縣台東市桂林北路124號", "台東市"),
    ("宜蘭縣羅東鎮民族路1號", "羅東鎮"),
    ("宜蘭縣大同鄉朝陽巷37號", "大同鄉"),
    ("", ""),
])
def test_township_of(address, township):
    assert township_of(address) == township


@pytest.mark.parametrize("address,street", [
    ("花蓮縣花蓮市達固湖灣大路23號", "達固湖灣大路"),
    ("台東縣台東市中華路一段721號", "中華路一段"),
    ("宜蘭縣宜蘭市中山路一段755號", "中山路一段"),
    ("宜蘭縣大同鄉朝陽巷37號", ""),
])
def test_street_of(address, street):
    assert street_of(address) == street


@pytest.mark.parametrize("name,facility", [
    ("[HUALIEN] 民義國小", "國小"),
    ("[YILAN] 中興國小附幼", "國小"),
    ("[TAITUNG] 台東女中", "高中"),
    ("[TAITUNG] 台東高中", "高中"),
    ("[YILAN] 宜蘭市國民運動中心", "體育場館"),
    ("[YILAN] 三星鄉綜合運動場", "體育場館"),
    ("[HUALIEN] 花蓮市立圖書館", "圖書館"),
    ("[YILAN] 大同鄉公所", "公所"),
    ("[TAITUNG] 台東市原住民文化會館", "會館"),
    ("[X] 不知名場地", "其他"),
])
def test_facility_of(name, facility):
    assert facility_of(name) == facility


def test_size_class_boundaries():
    # 消防署資料的收容人數中位數 70：分界在 50 / 150
    assert size_class_of(30) == "小型"
    assert size_class_of(50) == "小型"
    assert size_class_of(51) == "中型"
    assert size_class_of(150) == "中型"
    assert size_class_of(151) == "大型"
    assert size_class_of(2000) == "大型"
    # 填 0 是來源沒提供，不是小型
    assert size_class_of(0) == "未提供"


def test_aliases_strip_owner_and_place():
    assert "花蓮體育館" in aliases_of("[HUALIEN] 花蓮縣立體育館")
    assert "羅東體育館" in aliases_of("[YILAN] 羅東鎮立體育館")
    assert "中正體育館" in aliases_of("[HUALIEN] 花蓮市中正體育館")
    assert "台東體育場" in aliases_of("[TAITUNG] 台東縣立體育場")


@pytest.mark.parametrize("address,road", [
    ("台東縣台東市四維路一段690號", "四維路"),
    ("台東縣台東市四維路二段23號", "四維路"),
    ("台東縣台東市桂林北路52巷120號", "桂林北路"),
    ("花蓮縣花蓮市國聯一路170號", "國聯一路"),
    ("花蓮縣花蓮市達固湖灣大路23號", "達固湖灣大路"),
    ("宜蘭縣大同鄉朝陽巷37號", ""),
    ("", ""),
])
def test_road_of_drops_section(address, road):
    from services.retrieval.shelter_profile import road_of
    assert road_of(address) == road


def test_aliases_do_not_keep_bare_ownership_suffix():
    # 「花蓮縣立體育館」去掉地名剩「立體育館」，這不是別名，會跟好幾間撞在一起
    for name in ["[HUALIEN] 花蓮縣立體育館", "[HUALIEN] 花蓮市立圖書館", "[YILAN] 羅東鎮立體育館", "[TAITUNG] 台東縣立體育場"]:
        for alias in aliases_of(name):
            assert not alias.startswith("立"), (name, alias)


def test_aliases_long_forms():
    assert aliases_of("[HUALIEN] 民義國小") == ["民義國民小學"]
    assert "台東女子高級中學" in aliases_of("[TAITUNG] 台東女中")
    assert "中興國民小學附設幼兒園" in aliases_of("[YILAN] 中興國小附幼")


def test_aliases_special_cases():
    aliases = aliases_of("[TAITUNG] 台東市原住民文化會館")
    assert "原住民文化會館" in aliases
    assert "台東市原民文化會館" in aliases
    assert "國民運動中心" in aliases_of("[YILAN] 宜蘭市國民運動中心")


def test_aliases_never_generic():
    generic = {"體育館", "體育場", "圖書館", "公所", "高中", "女中", "國小", "國中", "運動中心", "會館"}
    for name in ["[HUALIEN] 花蓮縣立體育館", "[HUALIEN] 花蓮市公所", "[TAITUNG] 台東高中",
                 "[YILAN] 宜蘭國小", "[YILAN] 宜蘭市國民運動中心", "[HUALIEN] 花蓮市立圖書館"]:
        for alias in aliases_of(name):
            assert alias not in generic, (name, alias)
            assert len(alias) >= 3, (name, alias)


def test_profile_bundle():
    p = profile("[YILAN] 礁溪國中", "宜蘭縣礁溪鄉礁溪路四段23號", 550)
    assert p == {
        "region": "宜蘭",
        "county": "宜蘭縣",
        "township": "礁溪鄉",
        "street": "礁溪路四段",
        "road": "礁溪路",
        "facility": "國中",
        "size_class": "大型",
        "aliases": ["礁溪國民中學"],
    }
    # 資料自帶鄉鎮時以它為準（地址可能寫別的鄉鎮），臺 / 台 統一成台
    assert profile("[TAITUNG] F Hotel", "臺東縣台東市大順路33號", 100, "卑南鄉")["township"] == "卑南鄉"
    assert profile("[TAITUNG] 臺東市公所", "臺東縣臺東市中山路", 100, "臺東市")["township"] == "台東市"


def test_facility_labels_come_from_the_rules_table():
    # 標籤詞彙是推導出來的，不是手寫的第三份清單——否則又多一個要人工同步的地方
    from services.retrieval.shelter_profile import FACILITY_LABELS, FACILITY_RULES
    assert FACILITY_LABELS == {label for _, label in FACILITY_RULES} | {"其他"}
    assert facility_of("[YILAN] 不認識的設施") == "其他"


def test_query_side_labels_all_exist_on_the_index_side():
    """
    query_rules 的「問句 → 標籤」和這裡的「名稱 → 標籤」方向不同不能合併，
    但標籤詞彙必須一致：查詢端用了索引端沒有的標籤，ChromaDB 的 where 會永遠篩不到，
    然後安靜地退回一般檢索，看起來像檢索品質差而不是設定錯誤。
    """
    from services.chat.query_rules import FACILITY_KEYWORDS
    from services.retrieval.shelter_profile import FACILITY_LABELS
    used = {label for _, labels in FACILITY_KEYWORDS for label in labels}
    assert used <= FACILITY_LABELS, used - FACILITY_LABELS


@pytest.mark.parametrize("name,label", [
    ("[HUALIEN] 大同活動中心", "活動中心"),
    ("[TAITUNG] 初鹿村多功能暨老人活動中心", "活動中心"),
    ("[HUALIEN] 勝安宮", "宗教場所"),
    ("[TAITUNG] 金崙溫泉長老教會", "宗教場所"),
    ("[YILAN] 佛光大學", "大學"),
    ("[TAITUNG] 大武鄉立托兒所", "幼兒園"),
    ("[TAITUNG] 關山鎮關山國民小學", "國小"),
    ("[HUALIEN] 禪光育幼院", "社福機構"),
    ("[HUALIEN] 南美崙營區", "軍事營區"),
    ("[TAITUNG] 知本金聯世紀酒店", "旅館"),
    ("[YILAN] 南澳村辦公處", "公所"),
])
def test_official_dataset_facility_types(name, label):
    """消防署資料一半以上是活動中心，還有宮廟、教會、托兒所；舊規則下 644 間有 505 間落在「其他」"""
    assert facility_of(name) == label


def test_details_text_skips_missing_fields():
    from models.shelter import Shelter
    s = Shelter("[TAITUNG] 大武國小", 350, 22.36, 120.9, hazards=("水災", "震災"), indoor=True, outdoor=False,
                vulnerable_ok=True, phone="089-791826", serves="大鳥村1-6鄰")
    text = details_text(s)
    assert "適用災害：水災、震災" in text and "室內場地" in text and "適合安置避難弱者" in text
    assert "聯絡電話：089-791826" in text and "預計收容：大鳥村1-6鄰" in text
    # 沒填就整句省略：寫「未知」模型會講成「不適合」
    assert details_text(Shelter("[YILAN] 甲", 10, 24.0, 121.0)) == ""
