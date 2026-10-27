"""防災知識文件：格式解析、實際語料的完整性、索引與檢索、聊天路由"""
from unittest.mock import MagicMock, patch

import chromadb
import pytest

from services import query_rules
from services.chat_service import (
    KNOWLEDGE_HEADER,
    KNOWLEDGE_SYSTEM_PROMPT,
    KNOWLEDGE_UNAVAILABLE,
    NO_KNOWLEDGE_REPLY,
    NO_RELEVANT_REPLY,
    SHELTER_HEADER,
    SYSTEM_PROMPT,
    ChatService,
)
from services.knowledge import (
    HAZARDS,
    KnowledgeChunk,
    KnowledgeFormatError,
    KnowledgeHit,
    KnowledgeStore,
    load_chunks,
    parse_file,
    sources_text,
)
from tests.test_chat_service import FakeRepo, FakeStream, ndjson

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

    def search(self, query, hazards=None, n=3):
        self.searched.append((query, hazards))
        if self._error:
            raise self._error
        return self._hits


def make_service(knowledge):
    vs = MagicMock()
    vs.search.return_value = "- 富里國小是花蓮地區的避難收容場所，容量 120 人。"
    vs.shelter_metadata.return_value = []
    return ChatService(vs, FakeRepo(), knowledge=knowledge)


def test_knowledge_question_gets_knowledge_context_filtered_by_hazard():
    knowledge = FakeKnowledge()
    svc = make_service(knowledge)
    context, early = svc.build_context("土石流紅色警戒是什麼意思")
    assert early is None
    assert context.startswith(KNOWLEDGE_HEADER) and "■ 土石流黃色警戒" in context
    assert SHELTER_HEADER not in context
    assert knowledge.searched == [("土石流紅色警戒是什麼意思", ["debris_flow"])]
    svc.vector_store.search.assert_not_called()


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


def test_knowledge_prompt_uses_knowledge_rules_and_system_prompt():
    svc = make_service(FakeKnowledge())
    context, _ = svc.build_context("土石流紅色警戒是什麼意思")
    prompt = svc.build_prompt("土石流紅色警戒是什麼意思", context)
    assert prompt.startswith(KNOWLEDGE_HEADER) and SHELTER_HEADER not in prompt
    assert svc._payload(prompt, False)["system"] == KNOWLEDGE_SYSTEM_PROMPT
    shelter_prompt = svc.build_prompt("富里鄉有哪些避難所", "- 富里國小")
    assert svc._payload(shelter_prompt, False)["system"] == SYSTEM_PROMPT


def test_stream_appends_sources_and_keeps_lines_quoting_the_material():
    """「公所」「避難處所」是資料原文：回答防護不能把引用知識資料的那一行當成編造的避難所拿掉"""
    svc = make_service(FakeKnowledge())
    reply = ndjson("紅色警戒時：\n", "1. 依鄉鎮市區公所與警消單位的安排前往安全的避難處所。\n", "2. 立即疏散。")
    with patch("services.chat_service.requests.post", return_value=FakeStream(reply)):
        text = svc.chat("土石流紅色警戒是什麼意思")
    assert "依鄉鎮市區公所與警消單位的安排" in text
    assert text.endswith("- 農村水保署〈警戒發布及作為〉 https://246.ardswc.gov.tw/DebrisFlow/AlertPublish")


def test_stream_drops_invented_shelter_lines_in_knowledge_answers():
    svc = make_service(FakeKnowledge())
    reply = ndjson("1. 立即疏散。\n", "2. 可以前往花蓮社區活動中心，容量 50 人。\n")
    with patch("services.chat_service.requests.post", return_value=FakeStream(reply)):
        text = svc.chat("土石流紅色警戒是什麼意思")
    assert "花蓮社區活動中心" not in text and "立即疏散" in text


def test_fixed_replies_have_no_sources():
    svc = make_service(FakeKnowledge(hits=[]))
    assert svc.chat("土石流紅色警戒是什麼意思") == NO_KNOWLEDGE_REPLY


def test_no_knowledge_reply_counts_as_an_abstention():
    from evals.gen_metrics import is_abstention
    assert is_abstention(NO_KNOWLEDGE_REPLY)
    assert NO_RELEVANT_REPLY != NO_KNOWLEDGE_REPLY
