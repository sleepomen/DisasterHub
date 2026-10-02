import json
from unittest.mock import MagicMock, patch
import pytest
from models.shelter import Shelter
from services.chat_service import (
    ChatService,
    display_name,
    GenerationTimeout,
    GENERIC_ERROR,
    AI_UNAVAILABLE,
    STREAM_INTERRUPTED,
)


class FakeStream:
    """模擬 requests 的串流回應：可以當 context manager，iter_lines() 吐 NDJSON 位元組"""

    def __init__(self, lines, error=None):
        self.lines = lines
        self.error = error
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def raise_for_status(self):
        pass

    def iter_lines(self):
        for line in self.lines:
            yield line.encode("utf-8") if isinstance(line, str) else line
        if self.error:
            raise self.error


def ndjson(*chunks, done=True):
    """Ollama 串流的樣子：一行一個片段，最後一行帶 done"""
    lines = [json.dumps({"response": c, "done": False}, ensure_ascii=False) for c in chunks]
    if done:
        lines.append(json.dumps({"response": "", "done": True}))
    return lines


class FakeRepo:
    def get_nearest_shelters(self, lat, lon, limit=5):
        return [{"name": "[HUALIEN] 花蓮縣立體育館", "distance_km": 1.2, "capacity": 1500, "current_ppl": 0, "remaining": 1500}]

    def get_all_shelters(self):
        return [
            Shelter("[HUALIEN] 甲", 100, 23.9, 121.6, 20),
            Shelter("[YILAN] 乙", 300, 24.7, 121.7, 0),
            Shelter("[TAITUNG] 丙", 200, 22.7, 121.1, 50),
        ]


class BrokenRepo:
    def get_all_shelters(self):
        raise RuntimeError("password=secret host=disaster_db")

    def get_nearest_shelters(self, *a, **k):
        raise RuntimeError("password=secret host=disaster_db")


@pytest.fixture
def svc():
    vs = MagicMock()
    vs.search.return_value = "RAG 結果"
    return ChatService(vs, FakeRepo())


def route(svc, msg):
    if svc._is_simulation_query(msg):
        return "simulation"
    if svc._is_geo_query(msg):
        return "geo"
    return "rag"


@pytest.mark.parametrize("msg,expected", [
    ("哪間避難所離我最近？緯度 23.99 經度 121.60", "geo"),
    ("影響範圍大小如何？", "simulation"),
    ("花蓮哪間避難所容量最大？", "rag"),
    ("哪些避難所受到影響？", "simulation"),
    ("目前受影響的避難所還有空間嗎？", "simulation"),
    ("避難所有提供飲水嗎", "rag"),
])
def test_intent_routing(svc, msg, expected):
    assert route(svc, msg) == expected


@pytest.mark.parametrize("msg,expected", [
    ("緯度 23.99 經度 121.60", (23.99, 121.60)),
    ("緯度：23.99，經度：121.60", (23.99, 121.60)),
    ("經度 121.60 緯度 23.99", (23.99, 121.60)),
    ("23.99, 121.60 附近", (23.99, 121.60)),
    ("半徑 5 公里內 10 人", None),
    ("23.5 121", None),
    ("最近的避難所在哪", None),
    ("緯度 40.5 經度 121.6", None),
])
def test_extract_coords(svc, msg, expected):
    assert svc._extract_coords(msg) == expected


def test_display_name_strips_region_tag():
    assert display_name("[HUALIEN] 花蓮縣立體育館") == "花蓮縣立體育館"
    assert display_name("花蓮縣立體育館") == "花蓮縣立體育館"
    assert display_name(None) == ""


def test_geo_query_without_coords_returns_prompt(svc):
    context, early = svc.build_context("離我最近的避難所")
    assert context is None
    assert "座標" in early


@pytest.mark.parametrize("msg", ["知本附近", "頭城鎮附近的學校", "中華路一段附近", "宜蘭市附近有哪些避難所"])
def test_geo_query_with_place_name_goes_to_retrieval(svc, msg):
    # 使用者已經講了地點，就交給檢索，不要回頭要座標
    context, early = svc.build_context(msg)
    assert early is None
    assert context == "RAG 結果"
    svc.vector_store.search.assert_called()


@pytest.mark.parametrize("msg", ["最近的避難所在哪", "附近有什麼避難所", "我附近可以去哪裡"])
def test_geo_query_without_any_place_still_asks_for_coords(svc, msg):
    context, early = svc.build_context(msg)
    assert context is None and "座標" in early


def test_prompt_tells_model_data_is_prefiltered(svc):
    from services.chat_service import SYSTEM_PROMPT
    assert "不可以說沒有資料" in SYSTEM_PROMPT
    prompt = svc.build_prompt("宜蘭有哪些避難所", "資料")
    assert "每間一行" in prompt
    assert "原樣引用" in prompt


def test_nearest_context_has_no_region_tag(svc):
    ctx, early = svc._get_nearest_context(23.99, 121.6)
    assert early is None
    assert "花蓮縣立體育館" in ctx
    assert "HUALIEN" not in ctx
    assert "1.2 公里" in ctx


def test_capacity_ranking_goes_through_rules_layer(svc):
    # 容量排名不再有獨立的關鍵字分支，統一由規則層抽條件、向量庫依 metadata 排序
    context, early = svc.build_context("花蓮市哪間國小容量最大")
    assert early is None and context == "RAG 結果"
    plan = svc.vector_store.search.call_args.kwargs["plan"]
    assert plan.order_by_capacity is True
    assert plan.capacity_order == "desc"
    assert plan.township == "花蓮市"
    assert plan.facilities == ["國小"]
    svc.build_context("容量由小到大排序")
    assert svc.vector_store.search.call_args.kwargs["plan"].capacity_order == "asc"


def test_simulation_context_includes_remaining(svc):
    svc.set_simulation({"type": "flood", "radius_km": 10, "impacted_count": 1, "impacted_shelters": [
        {"name": "[TAITUNG] 丙", "capacity": 200, "current_ppl": 50, "remaining": 150}]})
    ctx, early = svc._get_simulation_context()
    assert early is None
    assert "淹水" in ctx
    assert "剩餘空間 150 人" in ctx
    assert "TAITUNG" not in ctx


def test_simulation_with_nothing_to_report_answers_without_the_llm(svc):
    # 「尚未執行模擬」這種句子本身就是答案，當成【避難所資料】餵進去只是多等一次生成
    ctx, early = svc._get_simulation_context()
    assert ctx is None and "尚未執行" in early
    svc.set_simulation({"type": "fire", "impacted_shelters": []})
    ctx, early = svc._get_simulation_context()
    assert ctx is None and "沒有受影響" in early


def test_prompt_includes_simulation_summary(svc):
    svc.set_simulation({"type": "earthquake", "lat": 23.9, "lon": 121.6, "radius_km": 5, "impacted_count": 3, "impacted_shelters": []})
    prompt = svc.build_prompt("有什麼建議", "資料")
    assert "【目前災害模擬結果】" in prompt
    assert "強震" in prompt
    svc.clear_simulation()
    assert "【目前災害模擬結果】" not in svc.build_prompt("有什麼建議", "資料")


def test_errors_do_not_leak_details():
    svc = ChatService(MagicMock(), BrokenRepo())
    assert svc._get_nearest_context(23.9, 121.6) == (None, GENERIC_ERROR)


def test_chat_stream_yields_ollama_chunks(svc):
    with patch("services.chat_service.requests.post", return_value=FakeStream(ndjson("建議前往", "乙避難所"))) as post:
        events = list(svc.chat_stream("避難所有提供飲水嗎"))
    assert events == [
        {"type": "delta", "text": "建議前往"},
        {"type": "delta", "text": "乙避難所"},
    ]
    body = post.call_args.kwargs["json"]
    assert "RAG 結果" in body["prompt"]
    # 串流要同時開在請求參數與 payload 上，少一邊 Ollama 就會整段等生成完才回
    assert body["stream"] is True
    assert post.call_args.kwargs["stream"] is True
    # 列舉題一次會塞 20 至 30 筆文件，沒設 num_ctx 會被 Ollama 預設值靜默截斷
    import config
    assert body["options"]["num_ctx"] == config.OLLAMA_NUM_CTX
    assert body["options"]["num_predict"] == config.OLLAMA_NUM_PREDICT


def test_chat_joins_stream_for_non_streaming_callers(svc):
    with patch("services.chat_service.requests.post", return_value=FakeStream(ndjson(" 建議前往", "乙 "))):
        assert svc.chat("避難所有提供飲水嗎") == "建議前往乙"


def test_stream_and_non_stream_send_the_same_options(svc):
    # 評測走 generate()、線上走 generate_stream()，模型設定一旦漂掉評測數字就不代表線上
    stream_payload = svc._payload("p", True)
    assert svc._payload("p", False) == {**stream_payload, "stream": False}


def test_early_reply_does_not_call_ollama(svc):
    with patch("services.chat_service.requests.post") as post:
        events = list(svc.chat_stream("高雄有哪些避難所"))
    assert post.call_count == 0
    assert len(events) == 1
    assert events[0]["type"] == "delta" and "高雄" in events[0]["text"]


def test_chat_handles_ollama_failure(svc):
    with patch("services.chat_service.requests.post", side_effect=ConnectionError("down")):
        events = list(svc.chat_stream("避難所有提供飲水嗎"))
    assert events == [{"type": "error", "text": AI_UNAVAILABLE}]
    with patch("services.chat_service.requests.post", side_effect=ConnectionError("down")):
        assert svc.chat("避難所有提供飲水嗎") == AI_UNAVAILABLE


def test_stream_broken_midway_keeps_text_and_adds_notice(svc):
    # 已經吐出去的字收不回來，所以保留並在後面補一句，而不是整段換成錯誤訊息
    stream = FakeStream(ndjson("甲避難所：剩餘 90 人", done=False), error=ConnectionError("斷線"))
    with patch("services.chat_service.requests.post", return_value=stream):
        events = list(svc.chat_stream("避難所有提供飲水嗎"))
    assert events[0] == {"type": "delta", "text": "甲避難所：剩餘 90 人"}
    assert events[-1] == {"type": "error", "text": STREAM_INTERRUPTED}


def test_stream_skips_unparsable_lines_and_surfaces_ollama_error(svc):
    stream = FakeStream([
        "這一行不是 JSON",
        json.dumps({"response": "甲", "done": False}),
        json.dumps({"error": "model not found"}),
    ])
    with patch("services.chat_service.requests.post", return_value=stream):
        events = list(svc.chat_stream("避難所有提供飲水嗎"))
    assert events[0] == {"type": "delta", "text": "甲"}
    assert events[-1] == {"type": "error", "text": STREAM_INTERRUPTED}


def test_stream_without_any_text_reports_unavailable(svc):
    with patch("services.chat_service.requests.post", return_value=FakeStream(ndjson())):
        assert list(svc.chat_stream("避難所有提供飲水嗎")) == [{"type": "error", "text": AI_UNAVAILABLE}]


def test_stream_stops_at_deadline(svc):
    # done 永遠不來時整段時間要被 OLLAMA_TIMEOUT 擋住，生成名額才不會被永久佔住
    endless = [json.dumps({"response": "字", "done": False}) for _ in range(5)]
    stream = FakeStream(endless)
    with patch("config.OLLAMA_TIMEOUT", 0),          patch("services.chat_service.requests.post", return_value=stream):
        with pytest.raises(GenerationTimeout):
            list(svc.generate_stream("prompt"))
        events = list(svc.chat_stream("避難所有提供飲水嗎"))
    assert events == [{"type": "delta", "text": "字"}, {"type": "error", "text": STREAM_INTERRUPTED}]
    # 連線要關掉，不能把 socket 留給下一次生成
    assert stream.closed


def test_refresh_occupancy_updates_simulation_snapshot(svc):
    svc.set_simulation({"type": "flood", "radius_km": 10, "impacted_count": 1, "impacted_shelters": [
        {"name": "[TAITUNG] 丙", "capacity": 200, "current_ppl": 50, "remaining": 150}]})
    svc.refresh_occupancy([Shelter("[TAITUNG] 丙", 200, 22.7, 121.1, 190)])
    ctx, _ = svc._get_simulation_context()
    assert "目前收容 190 人" in ctx
    assert "剩餘空間 10 人" in ctx


def test_refresh_occupancy_without_simulation_is_noop(svc):
    svc.refresh_occupancy([Shelter("[TAITUNG] 丙", 200, 22.7, 121.1, 190)])
    assert svc.latest_simulation == {}


def test_refresh_occupancy_recomputes_population_totals(svc):
    # 回寫後總剩餘空間要等於逐筆加總；可安置 / 缺口保留模擬當下的規劃值，另外記已安置人數
    svc.set_simulation({
        "type": "earthquake", "radius_km": 10, "impacted_count": 2,
        "impacted_shelters": [
            {"name": "[HUALIEN] 甲", "capacity": 100, "current_ppl": 0, "remaining": 100},
            {"name": "[HUALIEN] 乙", "capacity": 200, "current_ppl": 50, "remaining": 150},
        ],
        "population": {"estimated_evacuees": 400, "total_remaining": 250, "placeable": 250, "shortfall": 150,
                       "fallback_estimate": False, "townships": []},
    })
    svc.refresh_occupancy([
        Shelter("[HUALIEN] 甲", 100, 23.9, 121.6, 100),
        Shelter("[HUALIEN] 乙", 200, 23.9, 121.6, 190),
    ])
    pop = svc.latest_simulation["population"]
    assert pop["initial_remaining"] == 250
    assert pop["total_remaining"] == 10
    assert pop["placed"] == 240
    assert pop["placeable"] == 250 and pop["shortfall"] == 150

    ctx, _ = svc._get_simulation_context()
    assert "模擬當下範圍內避難所剩餘空間合計約 250 人" in ctx
    assert "疏散已安置約 240 人" in ctx
    assert "目前範圍內避難所剩餘空間合計約 10 人" in ctx
    assert "剩餘空間 0 人" in ctx and "剩餘空間 10 人" in ctx

    # 再回寫一次，基準值不能被覆蓋
    svc.refresh_occupancy([
        Shelter("[HUALIEN] 甲", 100, 23.9, 121.6, 100),
        Shelter("[HUALIEN] 乙", 200, 23.9, 121.6, 200),
    ])
    pop = svc.latest_simulation["population"]
    assert pop["initial_remaining"] == 250
    assert pop["total_remaining"] == 0
    assert pop["placed"] == 250


def test_format_people_uses_wan_for_large_numbers():
    from services.chat_service import format_people
    assert format_people(175000) == "約 17.5 萬人"
    assert format_people(2100) == "約 2100 人"
    assert format_people(None) == "不明"


def test_simulation_summary_and_context_include_population(svc):
    svc.set_simulation({
        "type": "earthquake", "lat": 23.977, "lon": 121.601, "radius_km": 10, "impacted_count": 1,
        "impacted_shelters": [{"name": "[HUALIEN] 甲", "capacity": 100, "current_ppl": 0, "remaining": 100}],
        "population": {
            "covered_population": 175000, "evacuation_ratio": 0.12, "estimated_evacuees": 21000,
            "fallback_estimate": False, "total_remaining": 100, "placeable": 100, "shortfall": 20900,
            "townships": [{"name": "花蓮市", "weight": 99000}, {"name": "吉安鄉", "weight": 60000}],
        },
    })
    summary = svc._simulation_summary()
    assert "約 17.5 萬人" in summary
    assert "主要為花蓮市、吉安鄉" in summary
    assert "預估需疏散約 2.1 萬人" in summary
    assert "12% 比例" in summary
    assert "收容缺口約 2.1 萬人" in summary
    ctx, _ = svc._get_simulation_context()
    assert "約 17.5 萬人" in ctx
    assert "受影響避難所共 1 個" in ctx


def test_population_lines_when_fallback_and_no_shortfall():
    from services.chat_service import population_lines
    lines = population_lines({"fallback_estimate": True, "estimated_evacuees": 157, "evacuation_ratio": 0.05,
                              "total_remaining": 900, "placeable": 157, "shortfall": 0, "townships": []})
    text = "\n".join(lines)
    assert "面積保底" in text
    assert "約 157 人" in text
    assert "足以安置" in text
    # 尚未回寫時沒有已安置那一行
    assert "已安置" not in text
    assert population_lines({}) == []


def test_chat_returns_unavailable_when_vector_search_fails(svc):
    # embedding 要打 Ollama，Ollama 掛掉時要回降級訊息，不能把例外丟到 /api/chat 變 500
    svc.vector_store.search.side_effect = ConnectionError("embed down")
    with patch("services.chat_service.requests.post") as post:
        assert svc.chat("避難所有提供飲水嗎") == AI_UNAVAILABLE
    post.assert_not_called()


def test_chat_returns_generic_error_when_context_building_fails(svc):
    with patch.object(svc, "build_context", side_effect=RuntimeError("password=secret")):
        reply = svc.chat("避難所有提供飲水嗎")
    assert reply == GENERIC_ERROR
    assert "secret" not in reply


# ── 規則層（B5）────────────────────────────────────────────
def test_out_of_scope_region_is_rejected_before_rag(svc):
    context, early = svc.build_context("高雄的避難所")
    assert context is None
    assert "高雄" in early and "宜蘭、花蓮、台東" in early
    svc.vector_store.search.assert_not_called()


def test_rag_query_passes_plan_to_vector_store(svc):
    context, early = svc.build_context("宜蘭有哪些國小")
    assert early is None
    assert context == "RAG 結果"
    plan = svc.vector_store.search.call_args.kwargs["plan"]
    assert plan.region == "宜蘭"
    assert plan.facilities == ["國小"]


def test_general_query_has_empty_plan(svc):
    svc.build_context("避難所有提供飲水嗎")
    plan = svc.vector_store.search.call_args.kwargs["plan"]
    assert plan.has_filter is False


def test_followup_routes_to_simulation_only_when_active(svc):
    assert svc._is_simulation_query("請給我疏散建議") is False
    svc.set_simulation({"type": "earthquake", "radius_km": 5, "impacted_count": 1, "impacted_shelters": [
        {"name": "[HUALIEN] 甲", "capacity": 100, "current_ppl": 0, "remaining": 100}]})
    assert svc._is_simulation_query("請給我疏散建議") is True
    context, early = svc.build_context("避難所還有空間嗎")
    assert early is None
    assert "受影響避難所共 1 個" in context
    svc.clear_simulation()
    assert svc._is_simulation_query("避難所還有空間嗎") is False


SIM = {"type": "earthquake", "radius_km": 5, "impacted_count": 1, "impacted_shelters": [
    {"name": "[HUALIEN] 甲", "capacity": 100, "current_ppl": 0, "remaining": 100}]}


def test_out_of_scope_is_rejected_even_during_simulation(svc):
    svc.set_simulation(SIM)
    context, early = svc.build_context("高雄有什麼疏散建議")
    assert context is None
    assert "高雄" in early and "宜蘭、花蓮、台東" in early


def test_geo_query_with_coords_wins_over_simulation_followup(svc):
    svc.set_simulation(SIM)
    context, early = svc.build_context("緯度 23.99 經度 121.60 附近有什麼建議")
    assert early is None
    assert "距離最近的避難所" in context
    assert "受影響避難所共" not in context


def test_geo_query_without_coords_during_simulation_uses_snapshot(svc):
    svc.set_simulation(SIM)
    context, early = svc.build_context("附近的避難所還有空間嗎")
    assert early is None
    assert "受影響避難所共 1 個" in context
    svc.clear_simulation()
    context, early = svc.build_context("附近的避難所還有空間嗎")
    assert context is None and "座標" in early


def test_snapshot_is_a_copy_so_readers_cannot_mutate_state(svc):
    svc.set_simulation(SIM)
    snap = svc._snapshot()
    snap["impacted_shelters"][0]["remaining"] = 0
    assert svc.latest_simulation["impacted_shelters"][0]["remaining"] == 100


def test_evacuation_advice_without_simulation_asks_to_run_one(svc):
    from services.chat_service import NO_SIMULATION_REPLY
    context, early = svc.build_context("請給我疏散建議")
    assert context is None
    assert early == NO_SIMULATION_REPLY
    svc.vector_store.search.assert_not_called()


def test_no_match_from_retrieval_becomes_fixed_reply_without_llm(svc):
    from services.chat_service import NO_RELEVANT_REPLY
    from services.vector_store import NO_MATCH, NO_DATA
    for sentinel in (NO_MATCH, NO_DATA):
        svc.vector_store.search.return_value = sentinel
        with patch("services.chat_service.requests.post") as post:
            assert svc.chat("今天天氣如何") == NO_RELEVANT_REPLY
        post.assert_not_called()
    svc.vector_store.search.return_value = "RAG 結果"


@pytest.mark.parametrize("msg,expected", [
    ("哪些避難所受到影響？", "尚未執行"),
    ("模擬結果如何", "尚未執行"),
])
def test_simulation_query_without_simulation_never_calls_ollama(svc, msg, expected):
    with patch("services.chat_service.requests.post") as post:
        events = list(svc.chat_stream(msg))
    assert post.call_count == 0
    assert len(events) == 1 and expected in events[0]["text"]


def test_nearest_query_failure_is_not_fed_to_the_model():
    # 以前查詢失敗的訊息會被當成【避難所資料】送進 prompt，模型只能對著一句錯誤訊息作文
    svc = ChatService(MagicMock(), BrokenRepo())
    with patch("services.chat_service.requests.post") as post:
        events = list(svc.chat_stream("離我最近的避難所 緯度 23.99 經度 121.60"))
    assert post.call_count == 0
    # 走的是檢索層的 early reply，所以型態是 delta；重點是它沒有進 prompt、也沒打 LLM
    assert events == [{"type": "delta", "text": GENERIC_ERROR}]


def test_nearest_query_with_no_results_replies_directly():
    class EmptyRepo:
        def get_nearest_shelters(self, lat, lon, limit=5):
            return []

    svc = ChatService(MagicMock(), EmptyRepo())
    with patch("services.chat_service.requests.post") as post:
        events = list(svc.chat_stream("離我最近的避難所 緯度 23.99 經度 121.60"))
    assert post.call_count == 0
    assert len(events) == 1 and "附近沒有找到" in events[0]["text"]
