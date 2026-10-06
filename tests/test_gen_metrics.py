from evals.gen_metrics import (
    build_catalog,
    format_issues,
    integers_in,
    is_abstention,
    mentioned_shelters,
    score_reply,
    suspected_invented_names,
)
from models.shelter import Shelter

SHELTERS = [
    Shelter("[YILAN] 宜蘭國小", 500, 24.7, 121.7, 0, "宜蘭縣宜蘭市崇聖街2號"),
    Shelter("[YILAN] 羅東鎮立體育館", 800, 24.6, 121.7, 100, "宜蘭縣羅東鎮體育路15號"),
    Shelter("[HUALIEN] 中正國小", 400, 23.9, 121.6, 0, "花蓮縣花蓮市中正路210號"),
    Shelter("[TAITUNG] 台東縣立體育館", 2000, 22.7, 121.1, 0, "台東縣台東市桂林北路124號"),
]
CATALOG = build_catalog(SHELTERS)
CONTEXT = (
    "- 宜蘭國小是宜蘭地區的避難收容場所，容量 500 人，目前收容 0 人，尚有 500 個空位。\n"
    "- 羅東鎮立體育館（別名：羅東體育館）是宜蘭地區的避難收容場所，容量 800 人，目前收容 100 人，尚有 700 個空位。"
)


def test_catalog_drops_aliases_shared_with_other_shelters():
    # 「立體育館」同時是羅東鎮立體育館與台東縣立體育館的子字串，不能拿來當任何一間的別名
    by_id = {e.id: e for e in CATALOG}
    assert "立體育館" not in by_id["[TAITUNG] 台東縣立體育館"].aliases
    assert "立體育館" not in by_id["[YILAN] 羅東鎮立體育館"].aliases
    assert "台東體育館" in by_id["[TAITUNG] 台東縣立體育館"].aliases
    assert mentioned_shelters("羅東鎮立體育館", CATALOG) == ["[YILAN] 羅東鎮立體育館"]


def test_mentioned_shelters_uses_display_name_and_aliases_in_order():
    reply = "建議前往羅東體育館，其次是宜蘭國小。"
    assert mentioned_shelters(reply, CATALOG) == ["[YILAN] 羅東鎮立體育館", "[YILAN] 宜蘭國小"]
    assert mentioned_shelters("沒有相關資料", CATALOG) == []


def test_invented_names_are_flagged_but_known_names_are_not():
    reply = "可以去羅東鎮立體育館或礁溪國中，宜蘭的體育館都可以。"
    assert suspected_invented_names(reply, CATALOG) == ["礁溪國中"]


def test_integers_skip_decimals_and_accept_thousands_separator():
    assert integers_in("座標 23.98, 121.60，容量 1,200 人，剩餘 700 人") == {1200, 700}


def test_abstention_and_format_checks():
    assert is_abstention("目前沒有相關資料。")
    assert not is_abstention("宜蘭國小可以收容 500 人。")
    assert format_issues("宜蘭國小可以收容 500 人。") == []
    assert "english" in format_issues("宜蘭國小 shelter 可以收容 500 人。")
    assert "emoji" in format_issues("宜蘭國小 🏫")
    assert any(i.startswith("simplified") for i in format_issues("这个避难所"))


def test_score_positive_case_full_marks():
    reply = "宜蘭地區有兩個避難所：1. 宜蘭國小，容量 500 人。2. 羅東鎮立體育館，容量 800 人，剩餘 700 人。"
    s = score_reply(reply, ["[YILAN] 宜蘭國小", "[YILAN] 羅東鎮立體育館"], CONTEXT, CATALOG)
    assert s["answer_recall"] == 1.0
    assert s["answer_precision"] == 1.0
    assert s["complete"] == 1.0
    assert s["hallucinated"] == 0.0
    assert s["numbers_supported"] == 1.0
    assert s["false_abstain"] == 0.0
    assert s["format_ok"] == 1.0


def test_score_detects_partial_recall_and_unsupported_numbers():
    reply = "宜蘭國小可以收容 650 人。"
    s = score_reply(reply, ["[YILAN] 宜蘭國小", "[YILAN] 羅東鎮立體育館"], CONTEXT, CATALOG)
    assert s["answer_recall"] == 0.5
    assert s["complete"] == 0.0
    assert s["unsupported_numbers"] == [650]
    assert s["numbers_supported"] == 0.0


def test_score_flags_mentions_outside_context_as_hallucination():
    # 中正國小是已知避難所，但檢索沒給模型看，講出來就是幻覺
    reply = "宜蘭國小與中正國小都可以。"
    s = score_reply(reply, ["[YILAN] 宜蘭國小"], CONTEXT, CATALOG)
    assert s["unsupported_mentions"] == ["[HUALIEN] 中正國小"]
    assert s["hallucinated"] == 1.0


def test_score_false_abstain_and_capacity_and_top1():
    s = score_reply("目前沒有相關資料。", ["[YILAN] 宜蘭國小"], CONTEXT, CATALOG, expect={"capacity": {"[YILAN] 宜蘭國小": 500}})
    assert s["false_abstain"] == 1.0
    assert s["capacity_stated"] == 0.0

    s = score_reply("宜蘭國小容量 500 人。", ["[YILAN] 宜蘭國小"], CONTEXT, CATALOG, expect={"capacity": {"[YILAN] 宜蘭國小": 500}})
    assert s["capacity_stated"] == 1.0
    assert s["false_abstain"] == 0.0

    expect = {"top1": "[YILAN] 羅東鎮立體育館"}
    scored = score_reply(
        "最大的是羅東體育館，其次宜蘭國小。", ["[YILAN] 羅東鎮立體育館"], CONTEXT, CATALOG, expect=expect
    )
    assert scored["top1_correct"] == 1.0
    assert score_reply("宜蘭國小最大，羅東體育館其次。", ["[YILAN] 羅東鎮立體育館"], CONTEXT, CATALOG, expect=expect)["top1_correct"] == 0.0


def test_score_negative_case():
    assert score_reply("本系統只涵蓋宜蘭、花蓮、台東三縣的避難所資料。", [], "", CATALOG)["abstain_correct"] == 1.0
    # 說沒資料卻還是列了避難所，不算正確拒答
    assert score_reply("目前沒有相關資料，但可以去宜蘭國小。", [], "", CATALOG)["abstain_correct"] == 0.0
    assert score_reply("高雄有高雄市立體育館可以避難。", [], "", CATALOG)["abstain_correct"] == 0.0


def test_echoed_query_words_and_numbers_are_not_penalised():
    from evals.gen_metrics import asks_capacity
    # 「縣立體育館」是使用者自己問的，回覆複述它不算編造；問題裡的 500 也不算未支持的數字
    reply = "縣立體育館的資訊如下：台東縣立體育館容量 2000 人。超過 500 人的只有這一間。"
    s = score_reply(reply, ["[TAITUNG] 台東縣立體育館"], "- 台東縣立體育館容量 2000 人", CATALOG,
                    query="容量超過500人的縣立體育館")
    assert s["invented"] == []
    assert s["unsupported_numbers"] == []
    # 「目前該國小」這種帶指示詞的片段也不是名稱
    assert suspected_invented_names("目前該國小收容 0 人", CATALOG) == []
    assert asks_capacity("宜蘭國小在哪裡") is False
    assert asks_capacity("宜蘭國小還有空位嗎") is True
    assert asks_capacity("宜蘭國小", "exact_name") is True


def test_capacity_check_is_skipped_for_location_questions():
    expect = {"capacity": {"[YILAN] 宜蘭國小": 500}}
    s = score_reply("宜蘭國小位於宜蘭縣宜蘭市崇聖街2號。", ["[YILAN] 宜蘭國小"], CONTEXT, CATALOG,
                    expect=expect, query="宜蘭國小在哪裡", check_capacity=False)
    assert "capacity_stated" not in s
    assert s["answer_recall"] == 1.0


def test_truncation_is_recorded():
    s = score_reply("宜蘭國小容量 500 人", ["[YILAN] 宜蘭國小"], CONTEXT, CATALOG, truncated=True)
    assert s["truncated"] == 1.0
