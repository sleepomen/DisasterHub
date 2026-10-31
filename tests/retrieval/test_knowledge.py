"""防災知識文件：格式解析、實際語料的完整性、索引與檢索、聊天路由"""
from unittest.mock import MagicMock, patch

import chromadb
import pytest

from services.chat import query_rules
from services.chat.replies import KNOWLEDGE_UNAVAILABLE, NO_KNOWLEDGE_REPLY, NO_RELEVANT_REPLY
from services.chat.service import ChatService
from services.retrieval.knowledge import (
    HAZARDS,
    KNOWLEDGE_HEADER,
    SHELTER_HEADER,
    KnowledgeChunk,
    KnowledgeFormatError,
    KnowledgeHit,
    KnowledgeStore,
    load_chunks,
    parse_file,
    sources_text,
)
from tests.chat.test_chat_service import FakeRepo, FakeStream, ndjson, text_only

SAMPLE = """# 颱風

## typhoon-warning｜颱風警報：海上與陸上
- hazard: typhoon
- kind: alert
- source: 中央氣象署《颱風百問》第 88 題
- url: https://example.gov.tw/typhoon.pdf
- retrieved: 2026-10-27

陸上颱風警報在 18 小時前發布。
- 這一行是正文裡的條列，不是欄位

## typhoon-after｜颱風過後
- hazard: typhoon
- kind: after
- source: 消防防災館〈颱風過後應注意事項〉
- url: https://example.gov.tw/a
- url: https://example.gov.tw/b
- retrieved: 2026-10-27

確定颱風遠離後才外出。
"""


def test_parse_file_reads_fields_body_and_multiple_urls():
    chunks = parse_file(SAMPLE, "typhoon.md")
    assert [c.id for c in chunks] == ["typhoon-warning", "typhoon-after"]
    first = chunks[0]
    assert (first.hazard, first.kind, first.retrieved) == ("typhoon", "alert", "2026-10-27")
    assert first.sources == [("中央氣象署《颱風百問》第 88 題", "https://example.gov.tw/typhoon.pdf")]
    # 空行之後的「- 」是正文，不能被當成欄位吃掉
    assert "這一行是正文裡的條列" in first.text
    assert chunks[1].sources == [
        ("消防防災館〈颱風過後應注意事項〉", "https://example.gov.tw/a"),
        ("消防防災館〈颱風過後應注意事項〉", "https://example.gov.tw/b"),
    ]


@pytest.mark.parametrize("broken", [
    SAMPLE.replace("- url: https://example.gov.tw/typhoon.pdf\n", ""),  # 沒有來源
    SAMPLE.replace("- hazard: typhoon\n- kind: alert", "- kind: alert"),  # 沒有災害
    SAMPLE.replace("- hazard: typhoon\n- kind: alert", "- hazard: volcano\n- kind: alert"),  # 不認得的災害
])
def test_parse_file_rejects_chunks_missing_required_fields(broken):
    with pytest.raises(KnowledgeFormatError):
        parse_file(broken, "typhoon.md")


def test_shipped_documents_are_complete():
    """實際的知識文件：每一段都有來源、擷取日期，五種災害都有，警戒說明也都在"""
    chunks = load_chunks()
    assert len(chunks) >= 30
    assert {c.hazard for c in chunks} == set(HAZARDS)
    for c in chunks:
        assert c.sources and all(url.startswith("https://") for _, url in c.sources), c.id
        assert 50 <= len(c.text) <= 400, c.id
    alerts = {c.hazard for c in chunks if c.kind == "alert"}
    assert alerts == set(HAZARDS)


def test_sources_text_lists_each_url_once_and_groups_urls_by_source():
    a = KnowledgeChunk("a", "甲", "typhoon", "alert", "內文", "2026-10-27", [("來源一", "https://x/1"), ("來源一", "https://x/3")])
    # 實測：《颱風百問》第 61 題、第 88 題是同一份 PDF，出處列了兩次
    b = KnowledgeChunk("b", "乙", "typhoon", "after", "內文", "2026-10-27", [("來源一第 2 題", "https://x/1"), ("來源二", "https://x/2")])
    assert sources_text([a, b]) == "資料來源：\n- 來源一 https://x/1 https://x/3\n- 來源二 https://x/2"


class KeywordEmbedding:
    """依幾個關鍵字出現次數產生向量，讓檢索結果可以預期"""
    KEYWORDS = ["颱風", "警報", "過後", "地震"]

    def __call__(self, input):
        return [[float(t.count(k)) for k in self.KEYWORDS] + [1e-3] for t in input]

    def name(self):
        return "keyword-test"

    def embed_query(self, input):
        return self(input)

    def embed_documents(self, input):
        return self(input)


@pytest.fixture
def store(tmp_path):
    (tmp_path / "typhoon.md").write_text(SAMPLE, encoding="utf-8")
    (tmp_path / "README.md").write_text("# 說明，不是語料", encoding="utf-8")
    s = KnowledgeStore(embedding_function=KeywordEmbedding(), client=chromadb.Client(), directory=tmp_path)
    s.build()
    yield s
    s.client.delete_collection("knowledge")


def test_store_builds_skips_readme_and_finds_the_closest_chunk(store):
    assert store.ready() and set(store.chunks) == {"typhoon-warning", "typhoon-after"}
    hits = store.search("颱風警報什麼時候發布")
    assert hits[0].chunk.id == "typhoon-warning"


def test_store_filters_by_hazard(store):
    assert store.search("颱風警報", hazards=["earthquake"]) == []


def test_store_rebuild_is_skipped_when_documents_did_not_change(store):
    collection = store.collection
    store.build()
    assert store.collection.name == collection.name
    with patch.object(store.client, "delete_collection") as dropped:
        store.build()
    dropped.assert_not_called()


# ---- 路由判斷 -------------------------------------------------------------

@pytest.mark.parametrize("query, hazards, mixed", [
    ("颱風來之前要準備什麼", ["typhoon"], False),
    ("土石流黃色警戒是什麼意思", ["debris_flow"], False),
    ("黃色警戒是什麼意思", [], False),
    ("震度5弱會怎樣", ["earthquake"], False),
    ("下雨多少算豪雨", ["flood"], False),
    ("颱風要準備什麼，花蓮市有哪些避難所", ["typhoon"], True),
])
def test_knowledge_request_detects_knowledge_questions(query, hazards, mixed):
    req = query_rules.knowledge_request(query)
    assert req is not None and req.hazards == hazards and req.mixed is mixed


@pytest.mark.parametrize("query", [
    "地震了要去哪裡",       # 要的是避難所
    "土石流要撤離到哪",     # 同上
    "颱風天哪裡可以躲",
    "宜蘭有哪些避難所",
    "颱風要怎麼申請補助",   # 有災害也有「怎麼」，但補助不在知識文件裡
    "颱風天會停班停課嗎",
    "火災要怎麼逃生",       # 知識文件沒有火災
])
def test_knowledge_request_leaves_other_questions_alone(query):
    assert query_rules.knowledge_request(query) is None


# ---- 聊天服務 -------------------------------------------------------------

CHUNK = KnowledgeChunk(
    "debris-flow-alert", "土石流黃色警戒、紅色警戒是什麼意思", "debris_flow", "alert",
    "紅色警戒：雨勢明顯，而且已經達到警戒值。民眾應依鄉鎮市區公所與當地警消單位的安排前往安全的避難處所。",
    "2026-10-27", [("農村水保署〈警戒發布及作為〉", "https://246.ardswc.gov.tw/DebrisFlow/AlertPublish")],
)


class FakeKnowledge:
    def __init__(self, hits=None, error=None, ready=True):
        self.chunks = {CHUNK.id: CHUNK}
        self._hits = [KnowledgeHit(CHUNK, 0.2)] if hits is None else hits
        self._error = error
        self._ready = ready
        self.searched = []

    def ready(self):
        return self._ready

    def ensure_ready(self):
        return self._ready

    def search(self, query, hazards=None, n=3):
        self.searched.append((query, hazards))
        if self._error:
            raise self._error
        return self._hits


def make_service(knowledge):
    vs = text_only(MagicMock())
    vs.search.return_value = "- 富里國小是花蓮地區的避難收容場所，容量 120 人。"
    vs.shelter_metadata.return_value = []
    return ChatService(vs, FakeRepo(), knowledge=knowledge)


def test_knowledge_question_is_answered_with_the_passage_itself():
    """純知識題不打模型：回答就是原文加出處（實測 3B 模型把撤離方向講反）"""
    knowledge = FakeKnowledge()
    svc = make_service(knowledge)
    context, early = svc.build_context("土石流紅色警戒是什麼意思")
    assert context is None
    assert early == (
        f"■ {CHUNK.title}\n{CHUNK.text}\n\n資料來源：\n"
        "- 農村水保署〈警戒發布及作為〉 https://246.ardswc.gov.tw/DebrisFlow/AlertPublish"
    )
    assert knowledge.searched == [("土石流紅色警戒是什麼意思", ["debris_flow"])]
    svc.vector_store.search.assert_not_called()


def test_knowledge_answer_never_calls_the_model():
    svc = make_service(FakeKnowledge())
    with patch("services.chat.llm_client.requests.post") as post:
        text = svc.chat("土石流紅色警戒是什麼意思")
    post.assert_not_called()
    assert text.startswith("■ 土石流黃色警戒") and "https://246.ardswc.gov.tw" in text


def test_knowledge_question_far_from_every_chunk_gets_fixed_reply():
    svc = make_service(FakeKnowledge(hits=[KnowledgeHit(CHUNK, 0.9)]))
    assert svc.build_context("土石流警戒什麼時候解除") == (None, NO_KNOWLEDGE_REPLY)


@pytest.mark.parametrize("knowledge", [None, FakeKnowledge(ready=False), FakeKnowledge(error=RuntimeError("ollama down"))])
def test_knowledge_unavailable_is_a_fixed_reply(knowledge):
    svc = make_service(knowledge)
    assert svc.build_context("土石流黃色警戒是什麼意思") == (None, KNOWLEDGE_UNAVAILABLE)


def test_mixed_question_gets_both_sections():
    svc = make_service(FakeKnowledge())
    context, early = svc.build_context("土石流紅色警戒要準備什麼，富里鄉有哪些避難所")
    assert early is None
    knowledge_part, shelter_part = context.split(SHELTER_HEADER)
    assert knowledge_part.startswith(KNOWLEDGE_HEADER)
    assert "富里國小" in shelter_part


def test_mixed_question_without_knowledge_hits_is_a_plain_shelter_question():
    svc = make_service(FakeKnowledge(hits=[]))
    context, early = svc.build_context("土石流紅色警戒要準備什麼，富里鄉有哪些避難所")
    assert early is None and KNOWLEDGE_HEADER not in context


def test_mixed_prompt_gives_the_model_only_the_shelter_half():
    """知識原文由程式送，模型只看到避難所資料與「富里鄉有哪些避難所」，沒機會改寫知識"""
    svc = make_service(FakeKnowledge())
    question = "土石流紅色警戒要準備什麼，富里鄉有哪些避難所"
    context, _ = svc.build_context(question)
    prompt = svc.build_prompt(question, context)
    assert KNOWLEDGE_HEADER not in prompt and CHUNK.text not in prompt
    assert "富里國小" in prompt
    assert "【使用者問題】\n富里鄉有哪些避難所\n" in prompt


def test_mixed_stream_sends_passage_then_shelters_then_sources():
    svc = make_service(FakeKnowledge())
    reply = ndjson("富里國小：富里鄉，容量 120 人。\n")
    with patch("services.chat.llm_client.requests.post", return_value=FakeStream(reply)):
        text = svc.chat("土石流紅色警戒要準備什麼，富里鄉有哪些避難所")
    passage = text.index(CHUNK.text)
    shelters = text.index("富里國小：富里鄉")
    sources = text.index("資料來源：")
    assert passage < shelters < sources


def test_mixed_question_with_no_shelter_data_still_gets_the_passage():
    svc = make_service(FakeKnowledge())
    svc.vector_store.search.return_value = "找不到相關避難所資料。"
    context, early = svc.build_context("土石流紅色警戒要準備什麼，富里鄉有哪些避難所")
    assert context is None and early.startswith(f"■ {CHUNK.title}")


def test_shelter_clauses_keeps_only_the_shelter_half():
    assert query_rules.shelter_clauses("颱風要準備什麼，花蓮市有哪些避難所") == "花蓮市有哪些避難所"
    assert query_rules.shelter_clauses("宜蘭有哪些避難所") == "宜蘭有哪些避難所"


def test_fixed_replies_have_no_sources():
    svc = make_service(FakeKnowledge(hits=[]))
    assert svc.chat("土石流紅色警戒是什麼意思") == NO_KNOWLEDGE_REPLY


def test_no_knowledge_reply_counts_as_an_abstention():
    from evals.lib.gen_metrics import is_abstention
    assert is_abstention(NO_KNOWLEDGE_REPLY)
    assert NO_RELEVANT_REPLY != NO_KNOWLEDGE_REPLY


@pytest.mark.parametrize("query, hazards", [
    ("住山坡地颱風前要撤離嗎", ["typhoon"]),   # 是非問句也是問做法
    ("地震時可以跑出去嗎", ["earthquake"]),
    ("什麼是土石流", ["debris_flow"]),
    ("土石流要撤離的時候要往哪個方向", ["debris_flow"]),
    ("海水突然退很多代表什麼", ["tsunami"]),    # 沒有「海嘯」兩個字
    ("海水倒灌要注意什麼", ["flood"]),          # 「海水倒灌」是淹水，不能再被算成海嘯
])
def test_knowledge_request_covers_misses_found_by_the_eval(query, hazards):
    req = query_rules.knowledge_request(query)
    assert req is not None and req.hazards == hazards


def test_yes_no_question_about_shelters_is_not_a_knowledge_question():
    assert query_rules.knowledge_request("颱風天避難所還有空位嗎") is None


def test_mixed_question_searches_knowledge_with_the_how_to_clause_only():
    req = query_rules.knowledge_request("颱風要準備什麼，花蓮市有哪些避難所")
    assert req.mixed and req.search_text == "颱風要準備什麼"
    knowledge = FakeKnowledge()
    make_service(knowledge).build_context("土石流紅色警戒要準備什麼，富里鄉有哪些避難所")
    assert knowledge.searched == [("土石流紅色警戒要準備什麼", ["debris_flow"])]


def test_mixed_question_asking_for_nearby_shelters_gets_passage_and_coordinate_request():
    """避難所那半句單獨看就是「附近收容所還有空位嗎」：要座標，不能交給模型憑空列避難所"""
    svc = make_service(FakeKnowledge())
    with patch("services.chat.llm_client.requests.post") as post:
        text = svc.chat("土石流紅色警戒要準備什麼，附近收容所還有空位嗎")
    post.assert_not_called()
    assert text.index(CHUNK.text) < text.index("請提供您的座標") < text.index("資料來源：")


class FlakyEmbedding(KeywordEmbedding):
    """前幾次逾時（Ollama 正忙），之後恢復"""

    def __init__(self, failures):
        self.failures = failures
        self.calls = 0

    def __call__(self, input):
        self.calls += 1
        if self.calls <= self.failures:
            raise TimeoutError("ollama busy")
        return super().__call__(input)


def test_failed_build_is_retried_but_not_on_every_question(tmp_path):
    """實測：啟動時 embedding 逾時，之後知識題一直回「目前無法使用」，要重啟服務才會好"""
    (tmp_path / "typhoon.md").write_text(SAMPLE, encoding="utf-8")
    ef = FlakyEmbedding(failures=1)
    s = KnowledgeStore(embedding_function=ef, client=chromadb.Client(), directory=tmp_path)
    try:
        with pytest.raises(TimeoutError):
            s.build()
        assert s.status() == (False, "not built")
        # 剛失敗：間隔內不重試，不會每一題都去打 embedding
        assert s.ensure_ready() is False and ef.calls == 1
        with patch("services.retrieval.knowledge.time.monotonic", return_value=10**9):
            assert s.ensure_ready() is True
        assert s.status() == (True, "2 passages")
    finally:
        s.client.delete_collection("knowledge")
