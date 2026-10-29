"""程式直接排好的避難所回答：清單、排名、概況、點名、篩選落空"""
from unittest.mock import MagicMock, patch

from services import query_rules
from services.chat_service import ChatService
from services.direct_answer import compose, shelter_detail, shelter_line
from services.vector_store import Hit, PlannedResult, SearchResult


def meta(name, township="花蓮市", capacity=100, remaining=80, **extra):
    return {
        "name": f"[HUALIEN] {name}", "county": "花蓮縣", "township": township,
        "address": f"花蓮縣{township}中山路1號", "capacity": capacity,
        "current_people": capacity - remaining, "remaining": remaining, **extra,
    }


def result(metas, **flags):
    hits = [Hit(name=m["name"], document="doc", distance=0.2, metadata=m) for m in metas]
    return PlannedResult(hits, total=flags.pop("total", len(hits)), **flags)


def test_shelter_line_and_unknown_capacity():
    assert shelter_line(meta("中華國小")) == "中華國小：花蓮市，容量 100 人，剩餘空位 80 人"
    # 來源填 0 不能寫成「容量 0 人、剩餘 0 人」：看起來像已經額滿
    assert shelter_line(meta("國福社區活動中心", capacity=0, remaining=0)) == "國福社區活動中心：花蓮市，收容人數資料未提供"


def test_named_answer_has_address_occupancy_and_designations():
    text = shelter_detail(meta("中華國小", hz_flood=True, hz_quake=True, indoor=True))
    assert text.splitlines() == [
        "中華國小",
        "- 地址：花蓮縣花蓮市中山路1號",
        "- 容量 100 人，目前收容 20 人，剩餘空位 80 人",
        "- 適用災害：水災、震災；室內",
    ]


def test_named_answer_lists_only_the_named_shelters():
    plan = query_rules.analyze("中華國小還有空位嗎")
    text = compose(plan, result([meta("中華國小")], named=True))
    assert text.startswith("中華國小\n") and "其他" not in text


def test_filtered_list_says_total_and_how_many_are_listed():
    plan = query_rules.analyze("花蓮市的國小")
    full = compose(plan, result([meta("中華國小"), meta("明義國小")], filtered=True))
    assert full.splitlines()[0] == "符合「花蓮市、國小」的避難所共 2 筆："
    assert full.splitlines()[1] == "- 中華國小：花蓮市，容量 100 人，剩餘空位 80 人"
    truncated = compose(plan, result([meta("中華國小")], filtered=True, total=12))
    assert truncated.startswith("符合「花蓮市、國小」的避難所共 12 筆，以下列出最相關的 1 筆：")


def test_ranked_list_is_numbered_in_capacity_order():
    plan = query_rules.analyze("花蓮市容量最大的避難所")
    metas = [meta("國風國中", capacity=800, remaining=800), meta("花蓮縣立體育場", capacity=527, remaining=527)]
    text = compose(plan, result(metas, filtered=True, ranked=True, total=40))
    lines = text.splitlines()
    assert lines[0] == "符合「花蓮市」的避難所依容量由大到小排序（共 40 筆，列出前 2 筆）："
    assert lines[1].startswith("1. 國風國中") and lines[2].startswith("2. 花蓮縣立體育場")


def test_unfiltered_ranking_covers_the_three_counties():
    plan = query_rules.analyze("容量最小的避難所")
    text = compose(plan, result([meta("小站", capacity=5, remaining=5)], ranked=True))
    assert text.startswith("東部三縣的避難所依容量由小到大排序")


def test_overview_gives_totals_distribution_and_how_to_narrow_down():
    plan = query_rules.analyze("花蓮有哪些避難所")
    overview = {
        "capacity": 20519, "remaining": 20000, "unknown_capacity": 3, "group_by": "鄉鎮",
        "groups": [("花蓮市", 40), ("吉安鄉", 22)],
    }
    text = compose(plan, result([meta("中華國小")], filtered=True, total=197, overview=overview))
    assert "共 197 筆，容量合計 20519 人，目前剩餘空間 20000 人（另有 3 筆未提供收容人數）" in text
    assert "依鄉鎮：花蓮市 40 間、吉安鄉 22 間。" in text
    assert "最相關的 1 筆：\n- 中華國小" in text
    assert "「花蓮市有哪些避難所」" in text


def test_fell_back_says_nothing_matched_first():
    plan = query_rules.analyze("花蓮市上千人的國小")
    text = compose(plan, result([meta("中華國小")], fell_back=True))
    assert text.startswith("沒有找到符合「花蓮市、國小、容量 ≥ 1000」條件的避難所。以下是最接近的 1 筆，僅供參考：")


def test_plain_semantic_results_are_left_to_the_model():
    plan = query_rules.analyze("知本附近")
    assert compose(plan, result([meta("知本國小")])) is None
    assert compose(plan, result([])) is None


def test_chat_returns_the_direct_answer_without_calling_the_model():
    vs = MagicMock()
    direct = "符合「花蓮市」的避難所共 1 筆：\n- 中華國小：花蓮市，容量 100 人，剩餘空位 80 人"
    vs.search_result.return_value = SearchResult("給模型的資料", direct)
    vs.shelter_metadata.return_value = []
    svc = ChatService(vs, None)
    with patch("services.chat_service.requests.post") as post:
        text = svc.chat("花蓮市有哪些避難所")
    post.assert_not_called()
    assert text.endswith("中華國小：花蓮市，容量 100 人，剩餘空位 80 人")


def test_mixed_question_shelter_half_is_direct_too():
    """知識原文＋程式排好的避難所清單＋出處：整題都不打模型"""
    from tests.test_knowledge import CHUNK, FakeKnowledge
    vs = MagicMock()
    direct = "符合「富里鄉」的避難所共 1 筆：\n- 富里國小：富里鄉，容量 120 人，剩餘空位 120 人"
    vs.search_result.return_value = SearchResult("資料", direct)
    vs.shelter_metadata.return_value = []
    svc = ChatService(vs, None, knowledge=FakeKnowledge())
    with patch("services.chat_service.requests.post") as post:
        text = svc.chat("土石流紅色警戒要準備什麼，富里鄉有哪些避難所")
    post.assert_not_called()
    assert text.index(CHUNK.text) < text.index("富里國小：富里鄉") < text.index("資料來源：")
