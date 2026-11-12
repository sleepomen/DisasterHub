"""小地名 → 座標：只在「地名＋附近」「我在＋地名」的句型裡認，同名的分開列，一般詞不認"""
import pytest

from models.shelter import Shelter
from services.geo.places import PlaceIndex, load_osm_places, place_label


def osm(name, lat, lon, forms=None):
    return {"name": name, "forms": forms or [name], "kind": "place=hamlet", "lat": lat, "lon": lon}


SHELTERS = [
    Shelter("[TAITUNG] 知本社區活動中心", 100, 22.70, 121.05, township="臺東市", village="知本里"),
    Shelter("[YILAN] 新社國小", 100, 24.80, 121.80, township="壯圍鄉", village="新社村"),
    Shelter("[HUALIEN] 新社社區活動中心", 100, 23.60, 121.52, township="豐濱鄉", village="新社村"),
    Shelter("[HUALIEN] 多良國小", 100, 22.50, 120.95, township="太麻里鄉", village="多良村"),
    Shelter("[YILAN] 南澳國小", 100, 24.46, 121.80, township="南澳鄉", village="南澳村"),
]


def index(places=(), shelters=SHELTERS):
    return PlaceIndex(list(places), list(shelters))


@pytest.mark.parametrize("question", [
    "七星潭附近有避難所嗎", "七星潭最近的避難所在哪", "我在七星潭，可以去哪裡避難", "住在七星潭要去哪裡", "七星潭一帶的避難所",
])
def test_place_is_recognised_in_location_phrases(question):
    found = index([osm("七星潭", 24.026, 121.632)]).find(question)
    assert found and found[0] == "七星潭"


@pytest.mark.parametrize("question", ["七星潭的風景好嗎", "七星潭國小在哪", "避難所的平台在哪"])
def test_place_names_outside_location_phrases_are_ignored(question):
    """兩個字的地名很多是一般詞（平台、和平），句型不對就不認"""
    assert index([osm("七星潭", 24.026, 121.632), osm("平台", 23.5, 121.4)]).find(question) is None


@pytest.mark.parametrize("question", ["我在龜山島，可以去哪裡避難", "住在龜山島要去哪裡"])
def test_a_place_name_that_starts_a_longer_name_is_not_that_place(question):
    """「我在龜山島」的「龜山」是龜山島，不是頭城的龜山里"""
    assert index([osm("龜山", 24.85, 121.87)]).find(question) is None


def test_village_suffix_after_a_place_name_is_still_that_place():
    found = index([osm("龜山", 24.85, 121.87)]).find("我在龜山里，可以去哪裡避難")
    assert found and found[0] == "龜山"


def test_longest_name_wins():
    found = index([osm("七星", 24.0, 121.6), osm("七星潭", 24.026, 121.632)]).find("七星潭附近")
    assert found[0] == "七星潭"


def test_entries_close_together_are_one_place():
    found = index([osm("馬太鞍", 23.674, 121.430), osm("馬太鞍", 23.684, 121.412)]).find("馬太鞍附近")
    assert len(found[1]) == 1


def test_names_scattered_in_many_places_are_treated_as_common_words():
    """「觀景台」「清水」分散在 4 處以上：當一般詞，不認"""
    places = [osm("觀景台", 22.5 + i * 0.5, 121.0) for i in range(4)]
    assert index(places).find("觀景台附近") is None


def test_same_name_in_two_places_lists_both_with_their_township():
    found = index().find("新社附近有避難所嗎")
    name, places = found
    assert name == "新社" and len(places) == 2
    assert sorted(place_label(p, 2) for p in places) == ["宜蘭縣壯圍鄉的新社", "花蓮縣豐濱鄉的新社"]


def test_naming_the_township_picks_one_of_them():
    _, places = index().find("豐濱新社附近有避難所嗎")
    assert [p.township for p in places] == ["豐濱鄉"]


def test_shelter_villages_fill_in_what_osm_lacks():
    """「多良」不在 OSM 地名裡：用避難所資料的村里「多良村」補上"""
    _, places = index().find("多良附近有避難所嗎")
    assert places[0].township == "太麻里鄉"


def test_township_names_are_left_to_the_rules():
    """南澳是鄉名，規則層已經認得，不當小地名（「南澳村」也一樣）"""
    assert index([osm("南澳", 24.46, 121.8)]).find("南澳附近") is None


def test_works_without_shelters():
    """路由評測用的假資料庫沒有避難所清單：只靠 OSM 地名也要認得出來"""
    found = PlaceIndex([osm("七星潭", 24.026, 121.632)], None).find("我在七星潭")
    assert found[1][0].township == ""


def test_the_bundled_place_file_covers_well_known_places():
    names = {f for p in load_osm_places() for f in p["forms"]}
    assert {"知本", "七星潭", "三仙台", "太魯閣", "南方澳", "石梯坪"} <= names
    # 鄉鎮名不在裡面
    assert not {"南澳", "玉里", "花蓮市", "池上鄉"} & names


def test_other_spellings_and_manual_places_are_found():
    """評測裡兩個來源都找不到的：OSM 寫「赤科山」、問的人寫「赤柯山」；梅花湖的水域在 OSM 沒有名字"""
    found = index([osm("赤科山", 23.388, 121.388)]).find("赤柯山附近有避難所嗎")
    assert found and found[0] == "赤柯山" and found[1][0].lat == 23.388
    name, places = index().find("梅花湖附近有避難所嗎")
    assert name == "梅花湖" and abs(places[0].lat - 24.645) < 0.01


XIKOU = [osm("溪口", 23.82, 121.47)]
SHOUFENG = [*SHELTERS, Shelter("[HUALIEN] 溪口國小", 100, 23.82, 121.47, township="壽豐鄉", village="溪口村")]


@pytest.mark.parametrize("question", ["蘭陽溪口附近有避難所嗎", "宜蘭的蘭陽溪口附近", "卑南溪口附近", "宜蘭溪口附近"])
def test_two_character_names_inside_longer_names_are_ignored(question):
    """「蘭陽溪口」「卑南溪口」都不是花蓮壽豐的「溪口」部落：前面緊接的字不是虛詞，也不是溪口所在的縣或鄉鎮"""
    assert index(XIKOU, SHOUFENG).find(question) is None


@pytest.mark.parametrize("question", [
    "溪口附近有避難所嗎", "請問溪口附近有避難所嗎", "我想去溪口附近看看", "到溪口最近的避難所",
    "壽豐溪口附近", "壽豐鄉溪口附近", "我在溪口", "颱風天，溪口附近",
])
def test_two_character_names_after_their_township_or_a_joining_word_still_count(question):
    found = index(XIKOU, SHOUFENG).find(question)
    assert found and found[0] == "溪口" and found[1][0].township == "壽豐鄉"


def test_longer_names_are_not_checked_for_what_comes_before():
    assert index([osm("七星潭", 24.026, 121.632)]).find("我想知道七星潭附近有什麼避難所")
