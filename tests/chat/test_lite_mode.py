"""輕量模式：不呼叫語言模型（LLM_ENABLED=false）、不用 Ollama 的字面向量（EMBEDDING_PROVIDER=lexical）"""
import math
from unittest.mock import MagicMock, patch

import pytest

import config
from models.shelter import Shelter
from services.chat.direct_answer import RELATED_LIST, related_answer
from services.chat.service import ChatService
from services.retrieval.chroma import make_client
from services.retrieval.embeddings import LexicalEmbedding, build_embedding_function
from services.retrieval.knowledge import KnowledgeChunk, KnowledgeStore
from services.retrieval.vector_store import Hit, SearchResult, VectorStore
from services.web import health
from tests.chat.test_chat_service import FakeRepo
from tests.retrieval.test_knowledge import SAMPLE


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_lexical_vectors_are_unit_length_and_deterministic():
    ef = build_embedding_function("lexical")
    assert isinstance(ef, LexicalEmbedding)
    a, b = ef(["淡江大學蘭陽校園", "淡江大學蘭陽校園"])
    assert a == b
    assert math.isclose(math.sqrt(sum(v * v for v in a)), 1.0)
    # 全是標點也要有向量，cosine 才算得出來
    assert math.isclose(math.sqrt(sum(v * v for v in ef(["？！"])[0])), 1.0)


def test_shared_characters_make_texts_closer():
    ef = LexicalEmbedding()
    query, near, far = ef(["淡江蘭陽校區", "淡江大學蘭陽校園", "台東縣立體育館"])
    assert cosine(query, near) > cosine(query, far)
    # 臺、台視為同一個字
    taiwan, short = ef(["臺東縣立體育館", "台東縣立體育館"])
    assert math.isclose(cosine(taiwan, short), 1.0)


def test_lexical_embedding_never_calls_ollama():
    with patch("services.retrieval.embeddings.requests.post") as post:
        LexicalEmbedding()(["地震了要去哪裡"])
    post.assert_not_called()


def test_vector_store_ranks_an_alias_first_with_lexical_vectors():
    shelters = [
        Shelter("[YILAN] 淡江大學蘭陽校園", 638, 24.8, 121.7),
        Shelter("[YILAN] 宜蘭國小", 500, 24.7, 121.7),
        Shelter("[TAITUNG] 台東縣立體育館", 2000, 22.7, 121.1),
    ]
    store = VectorStore(embedding_function=LexicalEmbedding(), collection_name="test_lite_lexical")
    store.build_index(shelters)
    assert store.retrieve("淡江蘭陽校區")[0].name == "[YILAN] 淡江大學蘭陽校園"


def meta(name, capacity=100, township="宜蘭市"):
    return {"name": name, "capacity": capacity, "remaining": capacity, "township": township}


def test_related_answer_lists_the_closest_shelters_and_says_no_model_picked_them():
    metas = [meta(f"[YILAN] 避難所{i}") for i in range(8)]
    text = related_answer(metas)
    assert text.startswith(f"跟問題最接近的 {RELATED_LIST} 間避難所（輕量模式")
    assert "- 避難所0：宜蘭市，容量 100 人，剩餘空位 100 人" in text
    assert "避難所5" not in text
    assert related_answer(metas, "en").startswith(f"The {RELATED_LIST} shelters closest to your question")


@pytest.fixture
def unfiltered_service():
    """沒有篩選條件的純語意題：檢索結果沒有程式排好的回答，原本要交給模型"""
    vs = MagicMock()
    vs.shelter_metadata.return_value = []
    hits = [Hit("[YILAN] 淡江大學蘭陽校園", "文件", 0.3, meta("[YILAN] 淡江大學蘭陽校園", 638, "礁溪鄉"))]
    vs.search_result.return_value = SearchResult("- 淡江大學蘭陽校園……", hits=hits)
    return ChatService(vs, FakeRepo())


def test_with_the_model_the_unfiltered_question_goes_to_the_model(unfiltered_service):
    context, early = unfiltered_service.build_context("淡江蘭陽校區")
    assert early is None and context.startswith("- 淡江大學蘭陽校園")


def test_lite_mode_answers_the_unfiltered_question_without_the_model(unfiltered_service, monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    with patch("services.chat.llm_client.requests.post") as post:
        reply = unfiltered_service.chat("淡江蘭陽校區")
    post.assert_not_called()
    assert "- 淡江大學蘭陽校園：礁溪鄉，容量 638 人，剩餘空位 638 人" in reply


@pytest.fixture
def knowledge(tmp_path):
    (tmp_path / "typhoon.md").write_text(SAMPLE, encoding="utf-8")
    store = KnowledgeStore(embedding_function=LexicalEmbedding(), client=make_client(), directory=tmp_path)
    store.build()
    yield store
    store.client.delete_collection("knowledge")


def test_a_full_title_in_the_question_gets_that_passage(knowledge):
    # 字面向量可能讓別段搶走第一名；照建議的標題再問一次一定要拿到那一段
    hits = knowledge.search("颱風警報：海上與陸上")
    assert [h.chunk.id for h in hits] == ["typhoon-warning"] and hits[0].distance == 0.0
    assert knowledge.search("颱風警報 海上與陸上？")[0].chunk.id == "typhoon-warning"


def test_related_titles_skip_the_sent_passage_and_titles_that_are_not_questions(knowledge):
    sent = [knowledge.chunks["typhoon-warning"]]
    assert knowledge.related_titles("颱風", ["typhoon"], sent, is_question=lambda t: True) == ["颱風過後"]
    assert knowledge.related_titles("颱風", ["typhoon"], sent, is_question=lambda t: False) == []


CHUNK = KnowledgeChunk("typhoon-after", "颱風過後要注意什麼", "typhoon", "after", "確定颱風遠離後才外出。", "2026-10-27")


class SuggestingKnowledge:
    chunks = {CHUNK.id: CHUNK}

    def ensure_ready(self):
        return True

    def search(self, query, hazards=None, n=2):
        from services.retrieval.knowledge import KnowledgeHit
        return [KnowledgeHit(CHUNK, 0.6)]

    def related_titles(self, query, hazards, sent, is_question, n=2):
        return [t for t in ("颱風來的時候要注意什麼",) if is_question(t)]


def test_lexical_knowledge_answer_suggests_other_topics(monkeypatch):
    monkeypatch.setattr(config, "LEXICAL", True)
    monkeypatch.setattr(config, "KNOWLEDGE_MAX_DISTANCE", config.LEXICAL_MAX_DISTANCE)
    svc = ChatService(MagicMock(), FakeRepo(), knowledge=SuggestingKnowledge())
    _, reply = svc.build_context("颱風過後要注意什麼")
    assert reply.startswith("■ 颱風過後要注意什麼")
    assert reply.endswith("如果這不是你要問的，也可以直接問：「颱風來的時候要注意什麼」")
    _, reply = svc.build_context("颱風過後要注意什麼", lang="en")
    assert "you can ask one of these instead: 「颱風來的時候要注意什麼」" in reply


def test_bge_m3_knowledge_answer_has_no_suggestions(monkeypatch):
    monkeypatch.setattr(config, "LEXICAL", False)
    svc = ChatService(MagicMock(), FakeRepo(), knowledge=SuggestingKnowledge())
    _, reply = svc.build_context("颱風過後要注意什麼")
    assert "如果這不是你要問的" not in reply


def test_ollama_is_not_checked_when_lite_mode_does_not_use_it(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENABLED", False)
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "lexical")
    with patch("services.web.health.requests.get") as get:
        assert health.check_ollama() == (True, "not used (lightweight mode)")
    get.assert_not_called()
    # 只關掉模型、仍用 bge-m3 時 Ollama 還是要在
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "ollama")
    with patch("services.web.health.requests.get", side_effect=ConnectionError()):
        assert health.check_ollama()[0] is False
