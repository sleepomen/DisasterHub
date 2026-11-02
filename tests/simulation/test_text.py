"""模擬追問的回答由程式組出來：分配、未安置、受影響、排除、建議"""
from models.shelter import Shelter
from services.chat.intent import simulation_topics
from services.simulation.text import exclusion_lines, hazard_lines, simulation_answer

SIM = {
    "type": "flood", "radius_km": 10, "impacted_count": 3,
    "impacted_shelters": [
        Shelter("[YILAN] 南澳國小", 200, 24.46, 121.80, 200),
        Shelter("[YILAN] 碧候國小", 100, 24.47, 121.79, 40),
        Shelter("[YILAN] 東澳國小", 300, 24.52, 121.83, 0),
    ],
    "population": {"covered_population": 6000, "evacuation_ratio": 0.22, "estimated_evacuees": 1320,
                   "fallback_estimate": False, "total_remaining": 60, "placeable": 260, "shortfall": 1060,
                   "townships": [{"name": "南澳鄉", "weight": 6000}]},
    "evacuation": {
        "assigned": {"[YILAN] 南澳國小": 200, "[YILAN] 碧候國小": 40}, "placed": 240, "unplaced": 1080,
        "distance_basis": "road", "mode": "walk", "unreachable": ["[YILAN] 東澳國小"],
        "near_hazard": {"[YILAN] 碧候國小": {"refs": ["宜縣DF147"], "distance_m": 137}}, "hazard": "debris_flow",
        "alerts": [{"ref_no": "宜縣DF115", "level": "red"}], "route_hazard": "debris_flow", "flows": [],
    },
}


def test_questions_are_sorted_into_topics():
    assert simulation_topics("這次模擬每間避難所分配了多少人？") == ["allocation"]
    assert simulation_topics("這次模擬還有多少人沒有被安置？") == ["unplaced"]
    assert simulation_topics("哪些避難所受影響？") == ["impacted"]
    assert simulation_topics("這次模擬有沒有避難所被排除？為什麼？") == ["excluded"]
    assert simulation_topics("每間分配多少人？有沒有被排除？") == ["excluded", "allocation"]
    # 建議、模擬結果：回整體摘要
    assert simulation_topics("有什麼建議") == []


def test_unplaced_answer_gives_exact_numbers_and_what_to_do():
    answer = simulation_answer(SIM, ["unplaced"])
    assert "需疏散 1320 人：已安置 240 人（分配到 2 間避難所），仍有 1080 人無法安置。" in answer
    assert "調度範圍外的避難所" in answer


def test_excluded_answer_lists_only_the_shelters_not_used():
    """評測裡模型把有分配到人的避難所說成被排除；程式只列真的沒列為目的地的"""
    answer = simulation_answer(SIM, ["excluded"])
    assert "聯外道路被封閉" in answer and "東澳國小" in answer
    # 碧候國小靠近潛勢範圍但照常分配：不算被排除，只在最後提醒
    excluded_part = "\n".join(exclusion_lines(SIM))
    assert "碧候國小" not in excluded_part
    assert "碧候國小（距土石流潛勢溪流影響範圍 宜縣DF147 約 137 公尺）" in answer


def test_no_exclusion_is_said_out_loud():
    sim = {**SIM, "evacuation": {"assigned": {"[YILAN] 南澳國小": 200}, "placed": 200, "unplaced": 0}}
    assert simulation_answer(sim, ["excluded"]).endswith("這次模擬沒有任何避難所被排除，受影響的 3 間都列入疏散分配的考慮。")


def test_notes_are_not_repeated():
    answer = simulation_answer(SIM, ["excluded"])
    for line in hazard_lines(SIM["evacuation"]):
        assert answer.count(line) == 1


def test_advice_summary_is_built_from_the_numbers():
    answer = simulation_answer(SIM, [])
    assert answer.startswith("模擬：淹水，半徑 10 公里，受影響避難所 3 間。")
    assert "預估需疏散約 1320 人" in answer
    assert "仍有 1080 人需要安置：請調度範圍外的避難所" in answer
    assert "疏散人數最多的是 南澳國小（200 人）、碧候國小（40 人）" in answer
    assert "目前有 1 條潛勢溪流發布土石流紅色警戒" in answer


def test_advice_points_to_free_space_when_everyone_fits():
    sim = {**SIM, "impacted_shelters": [Shelter("[YILAN] 南澳國小", 200, 24.46, 121.80, 50)],
           "evacuation": {"assigned": {"[YILAN] 南澳國小": 50}, "placed": 50, "unplaced": 0}}
    assert "目前還有 150 個空位，最多的是 南澳國小（150 人）" in simulation_answer(sim, [])


def test_two_topics_in_one_question_get_both_sections():
    answer = simulation_answer(SIM, simulation_topics("每間分配多少人？有沒有被排除？"))
    assert "南澳國小：分配 200 人" in answer and "聯外道路被封閉" in answer


def test_answers_in_english():
    answer = simulation_answer(SIM, ["allocation", "unplaced"], "en")
    assert answer.startswith("Simulation: flood, 10 km radius, 3 shelters affected.")
    assert "Evacuation plan (2 shelters, shortest total time on foot for everyone, never over a shelter's free space):" in answer
    assert "- 南澳國小: 200 people" in answer
    assert "1320 people need to evacuate: 240 are placed in 2 shelters and 1080 could not be placed." in answer
    assert "No safe road leads to these shelters" in answer
    summary = simulation_answer(SIM, [], "en")
    assert "Population in the affected area: about 6,000 people (mainly Nan'ao Township)." in summary
    assert "Suggestions:" in summary
    listing = simulation_answer(SIM, ["impacted"], "en")
    assert "3. 東澳國小: capacity 300, sheltering 0, 300 spaces left (no safe road; not used as a destination)" in listing
