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
    assert simulation_topics("開車疏散哪幾段路會塞？要多久才能清空？") == ["bottlenecks"]
    # 「塞不下」問的是容量，不是塞車
    assert simulation_topics("避難所塞不下怎麼辦") == []
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


def test_short_hazard_crossings_are_given_in_metres():
    """道路擦過範圍邊界只有幾公尺時，不能寫成「合計 0.0 公里」"""
    from services.simulation.text import route_lines
    plan = {"route_hazard": "debris_flow", "flows": [{"hazard_m": 3}, {"hazard_m": 1}, {"hazard_m": 0}]}
    assert "其中 2 條路線沒有更安全的道路，仍有部分路段經過災害範圍（合計 4 公尺）。" in route_lines(plan)
    assert "for 4 m in total" in " ".join(route_lines(plan, "en"))
    long = {"route_hazard": "debris_flow", "flows": [{"hazard_m": 1500}, {"hazard_m": 710}]}
    assert "（合計 2.21 公里）" in " ".join(route_lines(long))


def test_unreachable_shelters_are_not_also_called_still_used():
    """聯外道路被封閉的避難所靠近潛勢範圍時，不能同時出現在「仍列為疏散目的地」裡"""
    plan = {**SIM["evacuation"], "unreachable": ["[YILAN] 碧候國小"]}
    lines = hazard_lines(plan)
    assert any("碧候國小" in line and "聯外道路被封閉" in line for line in lines)
    assert not any("仍列為疏散目的地" in line and "碧候國小" in line for line in lines)
    assert not any("still used" in line and "碧候國小" in line for line in hazard_lines(plan, "en"))



BOTTLENECKS = {
    "top": [
        {"label": "台9 礁溪路一段", "bridges": [], "vehicles": 1000, "capacity_vph": 1450, "hours": 0.69},
        {"label": "台11 花蓮大橋", "bridges": ["花蓮大橋"], "vehicles": 2900, "capacity_vph": 1450, "hours": 2.0},
    ],
    "max_hours": 2.0, "vehicles": 3900, "local_people": 9680, "uncounted_people": 78,
    "people_per_vehicle": 2.5, "end_excluded_m": 500,
}
DRIVE = {**SIM, "evacuation": {**SIM["evacuation"], "mode": "drive", "bottlenecks": BOTTLENECKS}}


def test_bottleneck_answer_lists_each_road_with_its_numbers():
    answer = simulation_answer(DRIVE, simulation_topics("哪幾段路會塞？"))
    assert "開車疏散最塞的 2 條路（每條路列最慢的一段）：" in answer
    # 一小時內寫分鐘
    assert "1. 台9 礁溪路一段：約 1,000 輛車，每小時可過 1,450 輛，約 41 分鐘才能全部通過" in answer
    assert "2. 台11 花蓮大橋：約 2,900 輛車，每小時可過 1,450 輛，約 2.0 小時才能全部通過（經過花蓮大橋）" in answer
    # 沒算進來的人要說出來，結論要說是下限
    assert "另有 9,680 人的路線太短（每條路線頭尾各 500 公尺不算）" in answer
    assert "78 人沒有道路路線" in answer
    assert "這是下限：假設所有人同時出發、每車 2.5 人" in answer


def test_bottlenecks_on_foot_say_why_and_how_to_see_them():
    answer = simulation_answer(SIM, ["bottlenecks"])
    assert "這次模擬是步行，沒有估算道路瓶頸" in answer and "改成開車再模擬一次" in answer


def test_bottlenecks_that_failed_or_found_nothing():
    failed = {**SIM, "evacuation": {**SIM["evacuation"], "mode": "drive", "bottlenecks": None}}
    assert "這次沒有算出道路瓶頸" in simulation_answer(failed, ["bottlenecks"])
    empty = {**BOTTLENECKS, "top": [], "local_people": 0, "uncounted_people": 0}
    nothing = {**SIM, "evacuation": {**SIM["evacuation"], "mode": "drive", "bottlenecks": empty}}
    assert "算不出哪段路會塞" in simulation_answer(nothing, ["bottlenecks"])


def test_advice_names_the_slowest_road_when_driving():
    assert "開車最塞的是 台9 礁溪路一段（約 41 分鐘才能全部通過）" in simulation_answer(DRIVE, [])
    assert "開車最塞" not in simulation_answer(SIM, [])


def test_bottlenecks_in_english():
    answer = simulation_answer(DRIVE, ["bottlenecks"], "en")
    assert "1. 台9 礁溪路一段: about 1,000 cars at 1,450 per hour, about 41 min to clear" in answer
    assert "(crosses 花蓮大橋)" in answer and "This is a lower bound" in answer
    assert "This simulation was on foot" in simulation_answer(SIM, ["bottlenecks"], "en")


def test_long_bridge_lists_are_cut_to_three():
    many = {**BOTTLENECKS, "top": [{**BOTTLENECKS["top"][0], "bridges": [f"{c}橋" for c in "甲乙丙丁戊"]}]}
    sim = {**DRIVE, "evacuation": {**DRIVE["evacuation"], "bottlenecks": many}}
    assert "（經過甲橋、乙橋、丙橋等 5 座橋）" in simulation_answer(sim, ["bottlenecks"])
    assert "(crosses 甲橋, 乙橋, 丙橋 and 2 more bridges)" in simulation_answer(sim, ["bottlenecks"], "en")
