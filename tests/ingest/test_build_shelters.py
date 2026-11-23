"""tools/data/build_shelters.py 的缺字補回：來源把罕用字寫成「?」，依鄉鎮換回原字"""
import json
import pathlib

from tools.data.build_shelters import MISSING_CHARS, TEXT_FIELDS, restore_chars

ROOT = pathlib.Path(__file__).resolve().parents[2]


def row(township_text: str) -> dict:
    return dict.fromkeys(TEXT_FIELDS, township_text)


def test_restores_known_characters_only_in_their_township():
    fixed = restore_chars(row("土?天主堂"), "達仁鄉")
    assert set(fixed.values()) == {"土坂天主堂"}
    assert restore_chars(row("豐泉里里?里"), "關山鎮")["預計收容村里"] == "豐泉里里壠里"
    # 別的鄉鎮的「?」不猜
    assert set(restore_chars(row("土?天主堂"), "卑南鄉").values()) == {"土?天主堂"}


def test_restored_village_names_are_real_moi_villages():
    villages = json.loads((ROOT / "data_reference" / "east_villages.json").read_text(encoding="utf-8"))["villages"]
    known = {(v["township"], v["village"]) for v in villages}
    restored = {(town, right + "村") for town, fixes in MISSING_CHARS.items() for right in fixes.values()}
    restored |= {(town, right + "里") for town, fixes in MISSING_CHARS.items() for right in fixes.values()}
    # 廍後是聚落名不是村里，其餘四個都要是內政部的村里
    assert {("達仁鄉", "土坂村"), ("達仁鄉", "台坂村"), ("綠島鄉", "公舘村"), ("關山鎮", "里壠里")} <= restored & known


def test_shelter_files_have_no_question_marks_left():
    for path in (ROOT / "data_for_refuge").glob("*_shelter.json"):
        for shelter in json.loads(path.read_text(encoding="utf-8")):
            for key in ("name", "village", "address", "serves"):
                assert "?" not in shelter[key], (path.name, shelter["name"], key)
