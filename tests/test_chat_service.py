from unittest.mock import MagicMock, patch
import pytest
from models.shelter import Shelter
from services.chat_service import ChatService, display_name, GENERIC_ERROR, AI_UNAVAILABLE


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
    if svc._is_capacity_query(msg):
        return "capacity"
    return "rag"


@pytest.mark.parametrize("msg,expected", [
    ("哪間避難所離我最近？緯度 23.99 經度 121.60", "geo"),
    ("影響範圍大小如何？", "simulation"),
    ("花蓮哪間避難所容量最大？", "capacity"),
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


def test_nearest_context_has_no_region_tag(svc):
    ctx = svc._get_nearest_context(23.99, 121.6)
    assert "花蓮縣立體育館" in ctx
    assert "HUALIEN" not in ctx
    assert "1.2 公里" in ctx


def test_capacity_context_filters_region(svc):
    ctx = svc._get_capacity_context("宜蘭哪間避難所容量最大")
    assert "宜蘭地區" in ctx
    assert "乙" in ctx
    assert "甲" not in ctx


def test_capacity_context_sorted_desc(svc):
    ctx = svc._get_capacity_context("容量最大")
    lines = [l for l in ctx.splitlines() if l[:1].isdigit()]
    assert lines[0].startswith("1. 乙")
    assert lines[1].startswith("2. 丙")
    assert lines[2].startswith("3. 甲")


def test_simulation_context_includes_remaining(svc):
    svc.set_simulation({"type": "flood", "radius_km": 10, "impacted_count": 1, "impacted_shelters": [
        {"name": "[TAITUNG] 丙", "capacity": 200, "current_ppl": 50, "remaining": 150}]})
    ctx = svc._get_simulation_context()
    assert "淹水" in ctx
    assert "剩餘空間 150 人" in ctx
    assert "TAITUNG" not in ctx


def test_simulation_context_when_none(svc):
    assert "尚未執行" in svc._get_simulation_context()
    svc.set_simulation({"type": "fire", "impacted_shelters": []})
    assert "沒有受影響" in svc._get_simulation_context()


def test_prompt_includes_simulation_summary(svc):
    svc.set_simulation({"type": "earthquake", "lat": 23.9, "lon": 121.6, "radius_km": 5, "impacted_count": 3, "impacted_shelters": []})
    prompt = svc.build_prompt("有什麼建議", "資料")
    assert "【目前災害模擬結果】" in prompt
    assert "強震" in prompt
    svc.clear_simulation()
    assert "【目前災害模擬結果】" not in svc.build_prompt("有什麼建議", "資料")


def test_errors_do_not_leak_details():
    svc = ChatService(MagicMock(), BrokenRepo())
    assert svc._get_capacity_context("容量最大") == GENERIC_ERROR
    assert svc._get_nearest_context(23.9, 121.6) == GENERIC_ERROR


def test_chat_calls_ollama_and_returns_response(svc):
    with patch("services.chat_service.requests.post") as post:
        post.return_value.json.return_value = {"response": " 建議前往乙 "}
        post.return_value.raise_for_status.return_value = None
        assert svc.chat("避難所有提供飲水嗎") == "建議前往乙"
        body = post.call_args.kwargs["json"]
        assert "RAG 結果" in body["prompt"]
        assert body["stream"] is False


def test_chat_handles_ollama_failure(svc):
    with patch("services.chat_service.requests.post", side_effect=ConnectionError("down")):
        assert svc.chat("避難所有提供飲水嗎") == AI_UNAVAILABLE


def test_refresh_occupancy_updates_simulation_snapshot(svc):
    svc.set_simulation({"type": "flood", "radius_km": 10, "impacted_count": 1, "impacted_shelters": [
        {"name": "[TAITUNG] 丙", "capacity": 200, "current_ppl": 50, "remaining": 150}]})
    svc.refresh_occupancy([Shelter("[TAITUNG] 丙", 200, 22.7, 121.1, 190)])
    ctx = svc._get_simulation_context()
    assert "目前收容 190 人" in ctx
    assert "剩餘空間 10 人" in ctx


def test_refresh_occupancy_without_simulation_is_noop(svc):
    svc.refresh_occupancy([Shelter("[TAITUNG] 丙", 200, 22.7, 121.1, 190)])
    assert svc.latest_simulation == {}


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
    ctx = svc._get_simulation_context()
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
    assert population_lines({}) == []


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


def test_evacuation_advice_without_simulation_asks_to_run_one(svc):
    from services.chat_service import NO_SIMULATION_REPLY
    context, early = svc.build_context("請給我疏散建議")
    assert context is None
    assert early == NO_SIMULATION_REPLY
    svc.vector_store.search.assert_not_called()
