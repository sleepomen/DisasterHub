"""英文模式（第一階段）：回答用英文，避難所名稱與地址保留中文"""
from unittest.mock import MagicMock, patch

import pytest

from services import query_rules
from services.chat_service import (
    NO_RELEVANT_REPLY,
    SYSTEM_PROMPT_EN,
    UNREVIEWED_NOTE,
    ChatService,
    hazard_lines,
    knowledge_answer,
    tr,
    unknown_shelter_reply,
)
from services.direct_answer import compose, shelter_detail, shelter_line
from services.i18n import TOWNSHIP_EN, place_en
from services.knowledge import KnowledgeChunk, KnowledgeFormatError, load_chunks, parse_translations
from services.shelter_profile import TOWNSHIPS
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


def test_every_township_has_an_english_name():
    towns = {t for ts in TOWNSHIPS.values() for t in ts}
    assert towns == set(TOWNSHIP_EN)
    assert place_en("臺東市") == "Taitung City" and place_en("花蓮縣") == "Hualien County"
    assert place_en("不存在的地方") == "不存在的地方"


def test_plan_description_in_english():
    plan = query_rules.analyze("花蓮市容量 300 人以上適用水災的國小")
    assert plan.describe("en") == "Hualien City, elementary schools, capacity ≥ 300, designated for floods"


def test_shelter_line_and_detail_keep_names_and_addresses_in_chinese():
    assert shelter_line(meta("中華國小"), "en") == "中華國小: Hualien City, capacity 100, 80 spaces left"
    assert shelter_line(meta("國福社區活動中心", capacity=0, remaining=0), "en") == "國福社區活動中心: Hualien City, capacity not provided"
    assert shelter_detail(meta("中華國小", hz_flood=True, indoor=True), "en").splitlines() == [
        "中華國小",
        "- Address: 花蓮縣花蓮市中山路1號",
        "- Capacity 100, currently sheltering 20, 80 spaces left",
        "- designated for floods; indoor",
    ]


def test_english_lists_rankings_and_overviews():
    plan = query_rules.analyze("花蓮市的國小")
    assert compose(plan, result([meta("中華國小")], filtered=True), "en").startswith(
        "1 shelter matches Hualien City, elementary schools:\n- 中華國小: Hualien City"
    )
    two = compose(plan, result([meta("中華國小"), meta("明義國小")], filtered=True), "en")
    assert two.startswith("2 shelters match Hualien City, elementary schools:")
    ranked = compose(query_rules.analyze("花蓮市最大的避難所"), result([meta("國風國中")], filtered=True, ranked=True, total=40), "en")
    assert ranked.startswith("Shelters matching Hualien City, by capacity, largest first (40 in total, top 1 shown):\n1. 國風國中")
    overview = {"capacity": 900, "remaining": 800, "unknown_capacity": 0, "group_by": "鄉鎮", "groups": [("花蓮市", 40), ("吉安鄉", 22)]}
    text = compose(query_rules.analyze("花蓮有哪些避難所"), result([meta("中華國小")], filtered=True, total=197, overview=overview), "en")
    assert "197 shelters match Hualien, with a total capacity of 900" in text
    assert "By township: Hualien City 40, Ji'an Township 22." in text
    # 範例問句給中文：第一階段英文模式只翻回答，問題仍是中文最準
    assert "「花蓮市有哪些避難所」" in text


def test_fixed_replies_and_dynamic_replies_in_english():
    assert tr(NO_RELEVANT_REPLY, "en").startswith("No relevant information.")
    assert tr(NO_RELEVANT_REPLY, "zh") == NO_RELEVANT_REPLY
    assert tr("不是固定句", "en") == "不是固定句"
    assert query_rules.out_of_scope_reply("高雄", "en").startswith("This system only covers shelters in Yilan, Hualien and Taitung")
    reply = unknown_shelter_reply("頭城國中", [meta("頭城國小", township="頭城鎮")], "en")
    assert reply.splitlines()[0] == "There is no shelter called 「頭城國中」 in this system's list."
    assert "頭城國小: Toucheng Township" in reply


def test_build_context_translates_fixed_replies():
    vs = MagicMock()
    vs.shelter_metadata.return_value = []
    vs.search_result.return_value = SearchResult("找不到相關避難所資料。")
    svc = ChatService(vs, None)
    assert svc.build_context("花蓮市有哪些避難所", "en")[1] == tr(NO_RELEVANT_REPLY, "en")
    assert svc.build_context("高雄的避難所", "en")[1].startswith("This system only covers")


def test_build_context_returns_the_english_direct_answer():
    vs = MagicMock()
    vs.shelter_metadata.return_value = []
    vs.search_result.return_value = SearchResult("資料", "中文清單", "English list")
    svc = ChatService(vs, None)
    assert svc.build_context("花蓮市有哪些避難所", "en") == (None, "English list")
    assert svc.build_context("花蓮市有哪些避難所") == (None, "中文清單")


def chunk(reviewed):
    return KnowledgeChunk(
        "debris-flow-alert", "土石流黃色警戒、紅色警戒是什麼意思", "debris_flow", "alert", "紅色警戒：立即疏散。",
        "2026-10-27", [("農村水保署", "https://246.ardswc.gov.tw/a")],
        title_en="What yellow and red debris-flow alerts mean", text_en="Red alert: evacuate immediately.",
        en_reviewed=reviewed,
    )


def test_unreviewed_translations_show_the_chinese_text_with_a_note():
    """翻錯的防災資訊比看不懂更危險：沒審過的翻譯不上線"""
    text = knowledge_answer([chunk(reviewed=False)], "en")
    assert text.startswith(UNREVIEWED_NOTE)
    assert "紅色警戒：立即疏散。" in text and "Red alert" not in text
    assert "Sources (in Chinese):" in text


def test_reviewed_translations_are_used():
    text = knowledge_answer([chunk(reviewed=True)], "en")
    assert text.startswith("■ What yellow and red debris-flow alerts mean\nRed alert: evacuate immediately.")
    assert UNREVIEWED_NOTE not in text


def test_parse_translations_and_shipped_translations():
    parsed = parse_translations("## a-b｜Title\n- reviewed: yes\n\nBody line.\n- not a field\n")
    assert parsed == {"a-b": ("Title", "Body line.\n- not a field", True)}
    chunks = load_chunks()
    # 每一段都有英文稿；審稿前全部是 reviewed: no
    assert all(c.text_en for c in chunks)


def test_translation_for_an_unknown_passage_is_rejected(tmp_path):
    (tmp_path / "typhoon.md").write_text(
        "## typhoon-a｜甲\n- hazard: typhoon\n- kind: alert\n- source: 來源\n- url: https://x\n- retrieved: 2026-10-27\n\n內文\n",
        encoding="utf-8",
    )
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "typhoon.md").write_text("## typhoon-b｜B\n- reviewed: no\n\nBody\n", encoding="utf-8")
    with pytest.raises(KnowledgeFormatError):
        load_chunks(tmp_path)


def test_model_paths_use_the_english_prompt():
    svc = ChatService(MagicMock(), None)
    prompt = svc.build_prompt("知本附近有避難所嗎", "- 知本國小", lang="en")
    assert "Write the answer in English, but keep every shelter name and address in Chinese." in prompt
    assert svc._payload(prompt, False, "en")["system"] == SYSTEM_PROMPT_EN
    assert svc._payload(prompt, False)["system"] != SYSTEM_PROMPT_EN


def test_simulation_notes_in_english():
    plan = {
        "hazard": "debris_flow", "excluded": {"[YILAN] 澳花國小": ["宜縣DF139"]},
        "undesignated": ["[HUALIEN] 甲"], "designation": "水災",
    }
    lines = hazard_lines(plan, "en")
    assert lines[0] == (
        "These shelters are inside a debris-flow impact zone and were not used as evacuation destinations: 澳花國小 (宜縣DF139)."
    )
    assert lines[1] == "This shelter is not designated for floods and was not used as an evacuation destination: 甲."


def test_chat_stream_passes_the_language_to_the_model():
    vs = MagicMock()
    vs.shelter_metadata.return_value = []
    vs.search_result.return_value = SearchResult("- 知本國小")
    svc = ChatService(vs, None)
    with patch.object(svc, "generate_stream", return_value=iter(["知本國小: Taitung City\n"])) as gen:
        text = svc.chat("知本附近有避難所嗎", "en")
    assert gen.call_args.args[1] == "en"
    assert text == "知本國小: Taitung City"
