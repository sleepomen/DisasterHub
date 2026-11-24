"""services/chat/english_query.py：英文問句換成規則層認得的中文關鍵字"""
import pytest

from services.chat import intent, query_rules
from services.chat.alert_answer import asks_current_alerts
from services.chat.english_query import is_english, rewrite


@pytest.mark.parametrize("query", [
    "宜蘭有哪些避難所",
    "花蓮市容量超過500人的國小",
    "台北 101 附近有避難所嗎",
    "Taipei 的避難所有哪些呢",  # 中文字比英文字母多，照中文處理
])
def test_chinese_questions_pass_through_unchanged(query):
    assert not is_english(query)
    assert rewrite(query) == query


def test_only_mostly_english_questions_are_rewritten():
    assert is_english("Which shelters are in Yilan?")
    assert not is_english("宜蘭有哪些避難所")
    assert not is_english("")


@pytest.mark.parametrize("query, expected", [
    ("Which shelters are in Yilan?", {"region": "宜蘭"}),
    ("Shelters in Luodong that can hold more than 500 people",
     {"region": "宜蘭", "township": "羅東鎮", "capacity_min": 501}),
    ("Elementary schools in Hualien City for at least 1,000 people",
     {"township": "花蓮市", "facilities": ["國小"], "capacity_min": 1000}),
    ("Small shelters in Dongshan with 300 people or fewer",
     {"township": "冬山鄉", "capacity_max": 300, "size_class": "小型"}),
    ("Which shelter in Taitung has the largest capacity?", {"region": "台東", "order_by_capacity": True}),
    ("Are there wheelchair accessible indoor shelters in Su'ao?",
     {"township": "蘇澳鎮", "indoor": True, "vulnerable": True}),
    ("Earthquake shelters in Ji'an", {"township": "吉安鄉", "hazard": "震災"}),
    ("Which gyms in Taitung City are shelters?", {"township": "台東市", "facilities": ["體育場館"]}),
    ("Shelters on Orchid Island", {"township": "蘭嶼鄉"}),
    ("Shelters in Green Island", {"township": "綠島鄉"}),
])
def test_english_conditions_become_the_same_filters(query, expected):
    plan = query_rules.analyze(rewrite(query))
    for key, value in expected.items():
        assert getattr(plan, key) == value, (key, rewrite(query))


def test_out_of_scope_and_off_topic_in_english():
    assert query_rules.analyze(rewrite("Shelters in Taipei")).out_of_scope == "台北"
    # 「New Taipei」要先於「Taipei」
    assert query_rules.analyze(rewrite("Shelters in New Taipei City")).out_of_scope == "新北"
    assert query_rules.analyze(rewrite("What's the weather in Hualien tomorrow?")).off_topic == "天氣"


def test_word_boundaries_apply_to_every_alternative():
    # 片語的每個選項都要有字邊界：少了的話「any」會比到「many」的後半，留下一個「m」
    assert "m" not in rewrite("How many people could not be placed?").replace("模擬", "")
    assert "有沒有" not in rewrite("How many people could not be placed?")


def test_near_puts_the_place_first():
    assert rewrite("Shelters near 知本").startswith("知本附近")
    assert rewrite("Shelters near Luodong").startswith("羅東鎮附近")
    # 認不得的英文小地名照樣放在「附近」前面，交給「找不到這個地方」那條路
    assert intent.place_before_geo_word(rewrite("Shelters near Zhiben")) == "Zhiben"
    assert intent.place_before_geo_word(rewrite("Where's the closest shelter to Qixingtan?")) == "Qixingtan"


def test_near_me_asks_for_coordinates_and_coordinates_are_read():
    for query in ("Where is the nearest shelter?", "Find the closest shelter to me"):
        text = rewrite(query)
        assert intent.is_geo_query(text) and intent.needs_coordinates(text), text
    assert intent.extract_coords(rewrite("Nearest shelter to 24.68, 121.77")) == (24.68, 121.77)
    text = rewrite("Shelters near latitude 23.98 longitude 121.60")
    assert intent.is_geo_query(text) and intent.extract_coords(text) == (23.98, 121.60)


def test_knowledge_and_alert_questions():
    request = query_rules.knowledge_request(rewrite("What should I do during an earthquake?"))
    assert request is not None and request.hazards == ["earthquake"] and not request.mixed
    assert query_rules.knowledge_request(rewrite("What does a yellow debris flow alert mean?")) is not None
    mixed = query_rules.knowledge_request(
        rewrite("What should I do in an earthquake, and which shelters in Hualien City have free spaces?")
    )
    assert mixed is not None and mixed.mixed
    assert asks_current_alerts(rewrite("Are there any debris flow alerts right now?"))
    # 問警戒等級的意思是知識題，不是在問現在有沒有警戒
    assert not asks_current_alerts(rewrite("What does a yellow debris flow alert mean?"))


def test_where_to_go_in_a_disaster_is_a_shelter_question():
    # 跟中文一樣：「淹水時要去哪裡」問的是避難所，不是應變須知
    assert query_rules.knowledge_request(rewrite("Where should I go during a flood?")) is None


def test_simulation_follow_ups():
    assert intent.simulation_topics(rewrite("How many people could not be placed?")) == ["unplaced"]
    assert intent.simulation_topics(rewrite("Which roads will be congested?")) == ["bottlenecks"]
    assert intent.is_explicit_simulation_query(rewrite("Which shelters are affected in the simulation?"))


def test_shelter_names_with_latin_letters_are_kept():
    assert "F Hotel" in rewrite("Where is F Hotel?", protected=["F Hotel"])
    assert "旅館" in rewrite("Where is F Hotel?")


def test_unknown_words_are_kept_and_function_words_dropped():
    text = rewrite("Can I borrow money somewhere?")
    assert "borrow" in text and "money" in text
    assert "can" not in text.lower().split() and "somewhere" not in text.lower()


def test_chinese_written_in_an_english_question_stays_separate():
    # 黏成「哪裡頭城國中」的話會抽到「裡頭城國中」
    assert query_rules.unknown_shelter_name(rewrite("Where is 頭城國中?"), ["頭城國小"]) == "頭城國中"
    assert query_rules.unknown_shelter_name(rewrite("What is the capacity of 頭城國中?"), ["頭城國小"]) == "頭城國中"
    # 名稱裡夾數字也是同一段
    assert "國軍805醫院" in rewrite("Where is 國軍805醫院?")


def test_every_out_of_scope_place_and_off_topic_word_has_an_english_name():
    from services.web.i18n import OFF_TOPIC_EN, OUT_OF_SCOPE_EN
    assert set(query_rules.OUT_OF_SCOPE_PLACES) <= set(OUT_OF_SCOPE_EN)
    assert set(query_rules.OFF_TOPIC_WORDS) <= set(OFF_TOPIC_EN)


def test_english_replies_name_the_topic_in_english():
    assert "the weather" in query_rules.off_topic_reply("天氣", "en")
    assert "Taipei" in query_rules.out_of_scope_reply("台北", "en")
    # 中文回覆不變
    assert "天氣" in query_rules.off_topic_reply("天氣")


def test_school_and_office_names():
    # 「Senior High School」不是在問長者
    plan = query_rules.analyze(rewrite("Yilan Senior High School gym"))
    assert plan.facilities == ["高中"] and not plan.vulnerable
    # 宜蘭的舊拼法
    assert query_rules.analyze(rewrite("National Ilan University gymnasium")).region == "宜蘭"
    # 「Taitung City」先換成台東市，剩下的「Office」單獨算公所
    plan = query_rules.analyze(rewrite("Taitung City Office"))
    assert plan.township == "台東市" and plan.facilities == ["公所"]
    # hall 不篩類別：展演廳、禮堂都可能叫 hall
    assert query_rules.analyze(rewrite("Luodong Performance Hall")).facilities == []


def test_english_street_names_go_to_the_unknown_place_reply():
    # 資料裡的路名只有中文；交給語意檢索只會拿別處的避難所來答（評測裡距離 0.45～0.63，門檻擋不住）
    assert intent.place_before_geo_word(rewrite("Shelters on Linsen Road")) == "Linsen Road"
    assert intent.place_before_geo_word(rewrite("Near Jieren Street")) == "Jieren Street"
    assert intent.place_before_geo_word(rewrite("Cihui 3rd Street")) == "Cihui 3rd Street"
    # 路名本身是鄉鎮時照鄉鎮篩選
    assert query_rules.analyze(rewrite("Dawu Street")).township == "大武鄉"


def test_unknown_english_place_reply_asks_for_chinese():
    from services.chat.replies import unknown_place_reply
    assert "only recognised in Chinese" in unknown_place_reply("Linsen Road", "en")
    assert "only recognised in Chinese" not in unknown_place_reply("龜山島", "en")
