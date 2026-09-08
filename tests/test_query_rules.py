import pytest
from services.query_rules import analyze, out_of_scope_reply, known_townships


def test_known_townships_come_from_population_model():
    towns = known_townships()
    assert towns["羅東鎮"] == "宜蘭"
    assert towns["花蓮市"] == "花蓮"
    assert towns["台東市"] == "台東"
    assert len(towns) == 29


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
    ("花蓮有哪些學校可以避難", ["國小", "國中", "高中"]),
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
        {"facility": {"$in": ["國小", "國中", "高中"]}},
    ]}
    # 有明確數字時不再重複加 size_class
    plan = analyze("宜蘭的小型避難所，容量300人以下")
    assert plan.to_where() == {"$and": [{"region": "宜蘭"}, {"capacity": {"$lte": 300}}]}
    assert analyze("小型避難所").to_where() == {"size_class": "小型"}


def test_describe_is_human_readable():
    assert analyze("羅東鎮的國小").describe() == "羅東鎮、國小"
    assert analyze("台東能收上千人的地方").describe() == "台東地區、容量 ≥ 1000"
