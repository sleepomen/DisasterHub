import pytest

from services.chat.query_rules import analyze, known_townships, out_of_scope_reply


def test_known_townships_cover_all_three_counties():
    towns = known_townships()
    assert towns["羅東鎮"] == "宜蘭"
    assert towns["花蓮市"] == "花蓮"
    assert towns["台東市"] == "台東"
    # 人口模型只有 29 個；消防署資料裡的卓溪、蘭嶼、海端也要篩得到
    assert towns["卓溪鄉"] == "花蓮" and towns["蘭嶼鄉"] == "台東"
    assert len(towns) == 41


def test_every_township_in_the_shelter_data_is_known():
    from services.ingest.fetcher import DataFetcher
    from services.retrieval.shelter_profile import normalize_place
    towns = known_townships()
    assert {normalize_place(s.township) for s in DataFetcher().get_shelters()} <= set(towns)


def test_township_with_traditional_tai_is_found():
    assert analyze("臺東市的避難所").township == "台東市"


@pytest.mark.parametrize("query,region,township", [
    ("宜蘭有哪些避難所", "宜蘭", None),
    ("花蓮縣的避難所", "花蓮", None),
    ("臺東地區可以去哪裡避難", "台東", None),
    ("羅東鎮的避難所", "宜蘭", "羅東鎮"),
    ("台東市有哪裡可以避難", "台東", "台東市"),
    ("花蓮市區最大的收容場所", "花蓮", "花蓮市"),
    ("礁溪", "宜蘭", "礁溪鄉"),
    ("冬山", "宜蘭", "冬山鄉"),
    ("壯圍有哪些", "宜蘭", "壯圍鄉"),
    ("知本附近", None, None),
    ("縣立體育館", None, None),
])
def test_region_and_township_extraction(query, region, township):
    plan = analyze(query)
    assert plan.region == region
    assert plan.township == township
    assert plan.out_of_scope is None


def test_township_full_name_beats_region_prefix():
    # 「台東市」要當鄉鎮篩選，而不是只抓到「台東」地區
    plan = analyze("台東市的避難所")
    assert plan.to_where() == {"township": "台東市"}


def test_ambiguous_stems_are_not_townships():
    assert analyze("這次疏散成功了嗎").township is None
    assert analyze("大同電鍋").township is None


@pytest.mark.parametrize("query,facilities", [
    ("花蓮有哪些學校可以避難", ["國小", "國中", "高中", "大學"]),
    ("宜蘭的活動中心", ["活動中心"]),
    ("花蓮有廟可以避難嗎", ["宗教場所"]),
    ("台東的托兒所", ["幼兒園"]),
    ("宜蘭的國小避難所", ["國小"]),
    ("台東的國中", ["國中"]),
    ("花蓮的體育館", ["體育場館"]),
    ("宜蘭運動中心", ["體育場館"]),
    ("鄉公所或市公所", ["公所"]),
    ("花蓮市政府可以避難嗎", ["公所"]),
    ("原民會館", ["會館"]),
    ("避難所有提供飲水嗎", []),
])
def test_facility_extraction(query, facilities):
    assert analyze(query).facilities == facilities


@pytest.mark.parametrize("query,cap_min,cap_max,size", [
    ("花蓮能收上千人的地方", 1000, None, None),
    ("台東容量超過500人的避難所", 501, None, None),
    ("宜蘭的小型避難所，容量300人以下", None, 300, "小型"),
    ("至少 800 人的避難所", 800, None, None),
    ("200人以上的國小", 200, None, None),
    ("不到100人的地方", None, 100, None),
    ("大型避難所", None, None, "大型"),
    ("宜蘭有哪些避難所", None, None, None),
])
def test_capacity_extraction(query, cap_min, cap_max, size):
    plan = analyze(query)
    assert plan.capacity_min == cap_min
    assert plan.capacity_max == cap_max
    assert plan.size_class == size


def test_superlative_orders_by_capacity():
    assert analyze("宜蘭最大的避難所").order_by_capacity is True
    assert analyze("台東可以容納最多人的地方").order_by_capacity is True
    assert analyze("宜蘭的避難所").order_by_capacity is False
    # 原本聊天服務關鍵字分支涵蓋的說法也要能命中
    for q in ("花蓮容量排名", "容量由大到小", "哪間容量最高"):
        assert analyze(q).order_by_capacity is True, q


def test_capacity_order_direction():
    assert analyze("宜蘭最大的避難所").capacity_order == "desc"
    assert analyze("容量由小到大").capacity_order == "asc"
    assert analyze("最小的避難所").capacity_order == "asc"
    assert analyze("容量最低的地方").order_by_capacity is True


@pytest.mark.parametrize("query,place", [
    ("高雄的避難所", "高雄"),
    ("台北車站附近哪裡可以避難", "台北"),
    ("澎湖有避難所嗎", "澎湖"),
    ("桃園機場", "桃園"),
    ("台中體育館", "台中"),
    ("新竹國小", "新竹"),
    ("屏東縣立體育館", "屏東"),
    ("嘉義市公所", "嘉義"),
])
def test_out_of_scope_places_are_rejected(query, place):
    plan = analyze(query)
    assert plan.out_of_scope == place
    assert plan.to_where() is None
    reply = out_of_scope_reply(place)
    assert place in reply and "宜蘭、花蓮、台東" in reply


def test_in_scope_mention_overrides_out_of_scope():
    # 「從台北到花蓮」是在問花蓮，不能拒答
    plan = analyze("從台北出發到花蓮哪裡可以避難")
    assert plan.out_of_scope is None
    assert plan.region == "花蓮"


def test_non_geographic_queries_have_no_filter():
    for q in ("今天天氣如何", "如何申請災害補助", "避難所有提供飲水嗎"):
        plan = analyze(q)
        assert plan.out_of_scope is None
        assert plan.has_filter is False
        assert plan.to_where() is None


def test_where_clause_composition():
    plan = analyze("花蓮容量超過500人的體育館")
    assert plan.to_where() == {"$and": [
        {"region": "花蓮"},
        {"facility": "體育場館"},
        {"capacity": {"$gte": 501}},
    ]}
    plan = analyze("宜蘭有哪些學校可以避難")
    assert plan.to_where() == {"$and": [
        {"region": "宜蘭"},
        {"facility": {"$in": ["國小", "國中", "高中", "大學"]}},
    ]}
    # 有明確數字時不再重複加 size_class
    plan = analyze("宜蘭的小型避難所，容量300人以下")
    assert plan.to_where() == {"$and": [{"region": "宜蘭"}, {"capacity": {"$lte": 300}}]}
    assert analyze("小型避難所").to_where() == {"size_class": "小型"}


def test_describe_is_human_readable():
    assert analyze("羅東鎮的國小").describe() == "羅東鎮、國小"
    assert analyze("台東能收上千人的地方").describe() == "台東地區、容量 ≥ 1000"


@pytest.mark.parametrize("query,road", [
    ("四維路", "四維路"),
    ("更生路上", "更生路"),
    ("桂林北路的避難所", "桂林北路"),
    ("中華路一段附近", "中華路"),
    ("達固湖灣大路", "達固湖灣大路"),
    ("宜蘭市中山路一段有避難所嗎", "中山路"),
    ("羅東鎮的國小", None),
    ("避難所路線怎麼走", None),
    ("走路可以到的避難所", None),
])
def test_road_extraction(query, road):
    assert analyze(query).road == road


def test_road_filter_composes_with_township():
    plan = analyze("宜蘭市中山路一段有避難所嗎")
    assert plan.has_filter
    assert plan.to_where() == {"$and": [{"township": "宜蘭市"}, {"road": "中山路"}]}
    assert plan.describe() == "宜蘭市、中山路"
    assert analyze("四維路").to_where() == {"road": "四維路"}


def test_off_topic_reply_is_detected_as_an_abstention():
    """
    兩個固定拒答句都必須被 evals/lib/gen_metrics.py 的 ABSTAIN_MARKERS 認出來，
    否則生成評測的 abstain_correct 會無聲下降——拒答做對了，分數卻掉。
    """
    from evals.lib.gen_metrics import is_abstention
    from services.chat.query_rules import off_topic_reply, out_of_scope_reply
    assert is_abstention(out_of_scope_reply("高雄"))
    assert is_abstention(off_topic_reply("天氣"))


def test_off_topic_needs_the_whole_sentence():
    from services.chat.query_rules import analyze
    # 黑名單命中但整句問的是避難所 → 不算離題
    assert analyze("下雨天要去哪個避難所").off_topic is None
    assert analyze("淹水時收容所在哪").off_topic is None
    # 沒有避難主題 → 離題
    assert analyze("花蓮的天氣").off_topic == "天氣"
    assert analyze("宜蘭的超商在哪").off_topic == "超商"
    # 範圍外縣市優先回報涵蓋範圍，比較精確
    plan = analyze("高雄的天氣")
    assert plan.out_of_scope == "高雄" and plan.off_topic is None


# 真實名單的一部分，涵蓋各種相近名稱的陷阱
KNOWN = [
    "頭城國小", "新生國中", "新生國小", "台東高中", "台東女中", "濤強國小", "中正國小",
    "台東縣立體育館", "台東市原住民文化會館", "原住民文化會館", "中興國小附幼", "宜蘭國小", "宜蘭國民小學",
]


@pytest.mark.parametrize("query,name", [
    ("頭城國中在哪裡", "頭城國中"),
    ("請問頭城國中", "頭城國中"),
    ("新生高中還有空位嗎", "新生高中"),           # 字首一樣、字尾不同 → 不同的學校
    ("羅東高中的容量多少", "羅東高中"),           # 跟台東高中只差一字，但「羅東」是地名，不是錯字
    ("花蓮縣立游泳池還有空位嗎", "花蓮縣立游泳池"),  # 資料裡根本沒有這類場所
])
def test_unknown_shelter_name_is_detected(query, name):
    from services.chat.query_rules import unknown_shelter_name
    assert unknown_shelter_name(query, KNOWN) == name


@pytest.mark.parametrize("query", [
    "頭城國小在哪裡",        # 存在
    "宜蘭國民小學",          # 別名
    "中興國小",              # 「中興國小附幼」的前半
    "原民會館",              # 簡稱：字依序都在「原住民文化會館」裡
    "涙強國小還有空位嗎",    # 錯一個字，字首不是地名 → 錯字，照常檢索
    "中正國少的容量",        # 字尾不是合法類型 → 抽不到名稱
    "宜蘭的國小有哪些",      # 問一類，不是點名
    "宜蘭有哪些國小",
    "花蓮市國小",            # 字首是鄉鎮全名 → 問一類
    "大型體育館",
    "最大的體育館",
])
def test_known_typo_abbreviation_and_category_questions_are_not_flagged(query):
    from services.chat.query_rules import unknown_shelter_name
    assert unknown_shelter_name(query, KNOWN) is None


def test_unknown_shelter_check_is_off_without_a_name_list():
    # 索引還沒建好時名單是空的；這時不能把每個名稱都說成不存在
    from services.chat.query_rules import unknown_shelter_name
    assert unknown_shelter_name("頭城國中在哪裡", []) is None


def test_no_positive_retrieval_question_is_flagged_as_unknown():
    """130 題檢索題庫的正例全部是真實存在的避難所，一題都不能被攔成「查無」"""
    import json
    import pathlib

    from services.chat.query_rules import unknown_shelter_name
    from services.chat.shelter_names import known_names_of
    from services.ingest.fetcher import DataFetcher
    from services.retrieval.vector_store import VectorStore

    names = known_names_of([VectorStore.build_metadata(s) for s in DataFetcher().get_shelters()])
    path = pathlib.Path(__file__).resolve().parents[2] / "evals" / "datasets" / "rag_eval.jsonl"
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    flagged = [c["query"] for c in cases if c["relevant"] and unknown_shelter_name(c["query"], names)]
    assert flagged == []


@pytest.mark.parametrize("query,expected", [
    ("涙強國小還有空位嗎", ("涙強國小", "濤強國小")),
    ("中政國小", ("中政國小", "中正國小")),
])
def test_one_character_typo_maps_to_the_only_close_shelter(query, expected):
    from services.chat.query_rules import typo_correction, unknown_shelter_name
    assert typo_correction(query, KNOWN) == expected
    assert unknown_shelter_name(query, KNOWN) is None


@pytest.mark.parametrize("query", [
    "中強國小",          # 跟濤強國小、中正國小都只差一字：猜哪間都可能錯，不改
    "羅東高中",          # 字首是地名 → 另一間學校，不是台東高中的錯字
    "台中體育館",        # 範圍外縣市，絕不能被改成台東體育館
    "屏東縣立體育館",
    "頭城國小在哪裡",    # 名稱正確，沒什麼好改
])
def test_typo_correction_leaves_ambiguous_and_other_places_alone(query):
    from services.chat.query_rules import typo_correction
    names = KNOWN + ["台東體育館"]
    assert typo_correction(query, names) is None


@pytest.mark.parametrize("query,expected", [
    ("中正國少的容量", ("中正國少", "中正國小")),
    ("花蓮縣立體育官", ("花蓮縣立體育官", "花蓮縣立體育館")),
])
def test_last_character_typo_maps_to_the_shelter(query, expected):
    from services.chat.query_rules import typo_correction
    names = KNOWN + ["花蓮縣立體育館", "花蓮體育館"]
    assert typo_correction(query, names) == expected


def test_full_name_and_alias_hitting_together_count_as_one_shelter():
    from services.chat.query_rules import typo_correction
    names = ["花蓮市中正體育館", "中正體育館"]
    assert typo_correction("花蓮市中正體育管", names) == ("花蓮市中正體育管", "花蓮市中正體育館")


def test_only_the_last_character_may_differ():
    # 「宜蘭的小」跟「宜蘭國小」也只差一個字，但那是倒數第二個字：這是「宜蘭的小型避難所」，不是錯字
    from services.chat.query_rules import typo_correction
    assert typo_correction("宜蘭的小型避難所，容量300人以下", KNOWN) is None


def test_no_retrieval_question_is_rewritten_as_a_typo():
    """130 題檢索題庫（含負例）都沒有錯字；任何一題被改寫都代表規則放得太寬"""
    import json
    import pathlib

    from services.chat.query_rules import typo_correction
    from services.chat.shelter_names import known_names_of
    from services.ingest.fetcher import DataFetcher
    from services.retrieval.vector_store import VectorStore

    names = known_names_of([VectorStore.build_metadata(s) for s in DataFetcher().get_shelters()])
    path = pathlib.Path(__file__).resolve().parents[2] / "evals" / "datasets" / "rag_eval.jsonl"
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rewritten = [c["query"] for c in cases if typo_correction(c["query"], names)]
    assert rewritten == []


@pytest.mark.parametrize("query", [
    "地震了要去哪裡", "颱風天哪裡可以躲", "家裡停電了可以去哪裡過夜", "哪裡可以收容很多人",
    "我們一家五口可以去哪",  # 沒有災害詞，只有「去哪」
    "知本附近", "女子中學",  # 跟避難所名稱共用兩個字
])
def test_questions_with_any_shelter_clue_pass(query):
    from services.chat.query_rules import has_shelter_clue
    assert has_shelter_clue(query, ["知本國小", "台東女子高級中學"])


@pytest.mark.parametrize("query", [
    "有沒有可以借錢的地方", "哪裡可以洗衣服", "怎麼聯絡里長",
    "有沒有人可以幫我顧小孩",  # 「人」不算線索
])
def test_questions_without_any_shelter_clue_are_caught(query):
    from services.chat.query_rules import has_shelter_clue
    assert not has_shelter_clue(query, ["知本國小", "台東女子高級中學"])


def test_no_positive_retrieval_question_lacks_a_clue_on_the_unfiltered_path():
    """無線索規則只管沒有篩選條件的路徑；那條路上 130 題檢索題庫的正例一題都不能被擋"""
    import json
    import pathlib

    from services.chat.query_rules import has_shelter_clue
    from services.chat.shelter_names import known_names_of
    from services.ingest.fetcher import DataFetcher
    from services.retrieval.vector_store import VectorStore

    names = known_names_of([VectorStore.build_metadata(s) for s in DataFetcher().get_shelters()])
    path = pathlib.Path(__file__).resolve().parents[2] / "evals" / "datasets" / "rag_eval.jsonl"
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    blocked = []
    for c in cases:
        plan = analyze(c["query"])
        unfiltered = not (plan.out_of_scope or plan.off_topic or plan.has_filter or plan.order_by_capacity)
        if c["relevant"] and unfiltered and not has_shelter_clue(c["query"], names):
            blocked.append(c["query"])
    assert blocked == []



def test_hazard_indoor_and_vulnerable_only_narrow_existing_filters():
    plan = analyze("花蓮地震時適合老人去的室內避難所")
    assert (plan.hazard, plan.indoor, plan.vulnerable) == ("震災", True, True)
    assert plan.to_where() == {"$and": [
        {"region": "花蓮"}, {"hz_quake": True}, {"indoor": True}, {"vulnerable_ok": True},
    ]}
    assert "適用震災" in plan.describe() and "適合避難弱者" in plan.describe()
    # 沒有地點：篩出 442 間適用震災的避難所沒有意義，照一般檢索
    plan = analyze("地震了要去哪裡")
    assert plan.hazard == "震災" and not plan.has_filter and plan.to_where() is None


@pytest.mark.parametrize("query,vulnerable", [
    ("宜蘭有適合長者的避難所嗎", True),
    ("行動不便的人可以去哪", True),
    ("羅東有無障礙的避難所嗎", True),
    ("大武老人會館在哪", False),  # 名稱裡的「老人」不是條件
])
def test_vulnerable_condition(query, vulnerable):
    assert analyze(query).vulnerable is vulnerable


def test_off_topic_word_inside_a_shelter_name_is_not_off_topic():
    from services.chat.query_rules import off_topic_word_is_a_name
    names = ["國軍805醫院", "元新溫泉飯店", "花蓮市公所"]
    assert analyze("805醫院在哪").off_topic == "醫院"
    assert off_topic_word_is_a_name("805醫院在哪", "醫院", names)
    assert off_topic_word_is_a_name("元新溫泉飯店還有空位嗎", "飯店", names)
    assert not off_topic_word_is_a_name("花蓮的醫院在哪", "醫院", names)
    assert not off_topic_word_is_a_name("附近有飯店嗎", "飯店", names)
    assert analyze("805醫院在哪", ignore_off_topic=True).off_topic is None


def test_analyze_skips_off_topic_when_the_word_belongs_to_a_shelter_name():
    """檢索評測與聊天層走同一個判斷：以前只有聊天層會豁免，評測裡「805醫院」一筆都沒撈"""
    names = ["國軍805醫院"]
    assert analyze("805醫院在哪", known_names=names).off_topic is None
    assert analyze("花蓮的醫院在哪", known_names=names).off_topic == "醫院"
