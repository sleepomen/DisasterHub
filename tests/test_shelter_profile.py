import pytest
from services.shelter_profile import (
    aliases_of,
    county_of,
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
    assert size_class_of(100) == "小型"
    assert size_class_of(300) == "小型"
    assert size_class_of(301) == "中型"
    assert size_class_of(800) == "中型"
    assert size_class_of(801) == "大型"
    assert size_class_of(2000) == "大型"


def test_aliases_strip_owner_and_place():
    assert "花蓮體育館" in aliases_of("[HUALIEN] 花蓮縣立體育館")
    assert "羅東體育館" in aliases_of("[YILAN] 羅東鎮立體育館")
    assert "中正體育館" in aliases_of("[HUALIEN] 花蓮市中正體育館")
    assert "台東體育場" in aliases_of("[TAITUNG] 台東縣立體育場")


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
        "facility": "國中",
        "size_class": "中型",
        "aliases": ["礁溪國民中學"],
    }
