"""防災知識評測的計分規則"""
from evals.run_knowledge_eval import (
    fact_recall,
    foreign_latin,
    novel_sentences,
    score_reply,
    summarize,
    unsupported_numbers,
)

CONTEXT = (
    "【防災知識資料】\n■ 颱風警報：海上警報與陸上警報\n"
    "陸上颱風警報：預測 7 級風暴風範圍可能侵襲臺灣本島陸上之前 18 小時發布。"
    "太平洋海嘯警報中心（PTWC）的報文經評估。"
)


def test_fact_recall_ignores_whitespace():
    assert fact_recall("陸上警報在18小時前發布", ["18 小時", "24 小時"]) == 0.5
    assert fact_recall("任何回答", []) is None


def test_enumerators_are_not_numbers_but_invented_numbers_are():
    reply = "1. 陸上警報在 18 小時前發布。\n2) 每 3 小時發布一次。"
    # 條列編號 1、2 不算；18 在資料裡；3 不在
    assert unsupported_numbers(reply, CONTEXT) == ["3"]


def test_novel_sentences_flags_sentences_without_a_source():
    reply = "陸上颱風警報在暴風範圍可能侵襲陸上之前 18 小時發布。\n這代表需要立即進行警戒和分級並確定避難措施。"
    novel, checked = novel_sentences(reply, CONTEXT)
    assert checked == 2
    assert novel == ["這代表需要立即進行警戒和分級並確定避難措施"]


def test_boilerplate_and_short_sentences_are_not_judged():
    reply = "依據防災知識資料回答如下：\n立即疏散。"
    assert novel_sentences(reply, CONTEXT) == ([], 0)


def test_latin_from_the_source_is_allowed():
    assert foreign_latin("依 PTWC 報文評估", CONTEXT) == []
    assert foreign_latin("請注意 typhoon 動態", CONTEXT) == ["typhoon"]


def test_score_reply_marks_abstention():
    scores = score_reply("目前沒有相關資料。", CONTEXT, ["18 小時"])
    assert scores["abstained"] and scores["facts"] == 0.0


def test_summarize_counts_held_negatives_and_routes():
    rows = [
        {"kind": "positive", "route_ok": True, "hit": True, "top1": True, "precision": 1.0,
         "sent": ["a"], "distances": [["a", 0.2]]},
        {"kind": "negative", "held": False, "sent": ["b"], "distances": [["b", 0.4]]},
        {"kind": "negative", "held": True, "sent": [], "distances": []},
        {"kind": "shelter", "route_ok": True, "sent": [], "distances": [["a", 0.35]]},
    ]
    summary = summarize(rows)
    assert summary["positive"]["hit"] == 1.0
    assert summary["negative"] == {"n": 2, "held": 0.5}
    assert summary["best_distance"]["positive"] == {"max": 0.2, "min": 0.2}


def test_inline_enumerators_are_not_numbers_but_decimals_are():
    """模型把條列寫在同一行時，2. 3. 是編號；32.7 是風速，要照樣檢查"""
    reply = "1. 先趴下。 2. 掩護頭頸。 3. 風速每秒 32.7 公尺。"
    assert unsupported_numbers(reply, "風速每秒 32.7 公尺") == []
    assert unsupported_numbers("2. 風速每秒 40.1 公尺。", "風速每秒 32.7 公尺") == ["40.1"]
