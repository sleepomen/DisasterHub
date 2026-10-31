"""
把災害模擬的結果寫成文字：給模型的 prompt 資料，以及由程式附在回答最後的說明。

人口估算、疏散分配、被排除或靠近潛勢範圍的避難所、路線繞開了什麼，數字都跟地圖動畫、資料庫是同一份
"""
from services.chat.shelter_names import display_name
from services.simulation.evacuation import HAZARD_LABELS
from services.web.i18n import HAZARD_EN, place_en

DISASTER_TYPE_LABELS = {"earthquake": "強震", "flood": "淹水", "fire": "火災"}


def format_people(n) -> str:
    """大數字用「萬」表示，模型比較不會抄錯位數：175000 → 約 17.5 萬人"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "不明"
    if n >= 10000:
        return f"約 {n / 10000:.1f} 萬人"
    return f"約 {n} 人"


def population_lines(population: dict) -> list[str]:
    """把模擬的人口估算整理成可以直接塞進 prompt 的幾行文字"""
    if not population:
        return []
    covered = population.get("townships") or []
    lines = []
    if population.get("fallback_estimate"):
        lines.append("影響範圍內沒有主要人口聚落（多為海域或無人山區），疏散人數以面積保底估算。")
    else:
        top = "、".join(t["name"] for t in covered[:4])
        lines.append(
            f"影響範圍涵蓋人口{format_people(population.get('covered_population'))}"
            + (f"（主要為{top}）" if top else "")
            + "。"
        )
    ratio = population.get("evacuation_ratio")
    ratio_text = f"（依災害類型以 {ratio * 100:.0f}% 比例估算）" if isinstance(ratio, (int, float)) else ""
    lines.append(f"預估需疏散{format_people(population.get('estimated_evacuees'))}{ratio_text}。")
    if "total_remaining" in population:
        # 可安置 / 缺口是模擬當下的規劃數字；疏散回寫後剩餘空間會變，兩者要分開講，
        # 否則總計會跟下方逐筆避難所的剩餘空間加總對不上
        initial = population.get("initial_remaining", population.get("total_remaining"))
        lines.append(
            f"模擬當下範圍內避難所剩餘空間合計{format_people(initial)}，"
            f"可安置{format_people(population.get('placeable'))}。"
        )
        shortfall = population.get("shortfall") or 0
        if shortfall > 0:
            lines.append(f"收容缺口{format_people(shortfall)}，需要調度範圍外的避難所或擴大收容。")
        else:
            lines.append("範圍內避難所空間足以安置全部疏散人口。")
        placed = population.get("placed") or 0
        if placed > 0:
            lines.append(
                f"疏散已安置{format_people(placed)}，"
                f"目前範圍內避難所剩餘空間合計{format_people(population.get('total_remaining'))}。"
            )
    return lines


def near_hazard_note(info: dict, label: str) -> str:
    return f"距{label} {'、'.join(info['refs'])} 約 {info['distance_m']} 公尺"


HAZARD_LABELS_EN = {"debris_flow": "a debris-flow impact zone"}


def hazard_lines_en(plan: dict) -> list[str]:
    """hazard_lines 的英文版（模擬結束後由程式附在回答最後的那幾行）"""
    plan = plan or {}
    excluded = plan.get("excluded") or {}
    near = plan.get("near_hazard") or {}
    label = HAZARD_LABELS_EN.get(plan.get("hazard"), "a hazard zone")
    lines = []
    if excluded:
        names = ", ".join(f"{display_name(name)} ({', '.join(refs)})" for name, refs in sorted(excluded.items()))
        lines.append(f"These shelters are inside {label} and were not used as evacuation destinations: {names}.")
    if near:
        names = ", ".join(
            f"{display_name(name)} ({', '.join(info['refs'])}, about {info['distance_m']} m away)"
            for name, info in sorted(near.items())
        )
        lines.append(f"These shelters are near {label} and are still used; take care during heavy rain: {names}.")
    undesignated_names = plan.get("undesignated") or []
    if undesignated_names:
        joined = ", ".join(display_name(n) for n in undesignated_names)
        designation = HAZARD_EN.get(plan.get("designation"), plan.get("designation") or "")
        count = len(undesignated_names)
        lines.append(
            f"{'This shelter is' if count == 1 else f'These {count} shelters are'} not designated for {designation} "
            f"and {'was' if count == 1 else 'were'} not used as an evacuation destination: {joined}."
        )
    return lines + route_lines(plan, "en")


def hazard_lines(plan: dict, lang: str = "zh") -> list[str]:
    """位在潛勢區域內而被排除的、以及靠近區域但照常分配的避難所"""
    if lang == "en":
        return hazard_lines_en(plan)
    plan = plan or {}
    excluded = plan.get("excluded") or {}
    near = plan.get("near_hazard") or {}
    label = HAZARD_LABELS.get(plan.get("hazard"), "災害潛勢區域")
    lines = []
    if excluded:
        names = "、".join(f"{display_name(name)}（{'、'.join(refs)}）" for name, refs in sorted(excluded.items()))
        lines.append(f"以下避難所位於{label}內，本次未列為疏散目的地：{names}。")
    if near:
        names = "、".join(
            f"{display_name(name)}（{near_hazard_note(info, label)}）" for name, info in sorted(near.items())
        )
        lines.append(f"以下避難所靠近{label}，仍列為疏散目的地，豪雨期間請留意：{names}。")
    lines += undesignated_lines(plan)
    lines += route_lines(plan)
    return lines


def route_lines(plan: dict, lang: str = "zh") -> list[str]:
    """路線繞開了什麼、還有哪些路線不得不經過災害範圍、哪些地區沒有安全道路可走"""
    plan = plan or {}
    en = lang == "en"
    lines = []
    alerts = plan.get("alerts") or []
    reds = sum(1 for a in alerts if a.get("level") == "red")
    yellows = len(alerts) - reds
    if alerts:
        lines.append(
            f"Debris-flow alerts in effect: {reds} red (roads inside are closed) and {yellows} yellow "
            "(roads inside are avoided where possible); evacuation routes account for them." if en
            else f"目前有 {reds} 條潛勢溪流發布土石流紅色警戒（範圍內道路封閉）、"
            f"{yellows} 條黃色警戒（範圍內道路盡量繞行），疏散路線已納入。"
        )
    hazard = plan.get("route_hazard")
    if hazard or alerts:
        through = [f for f in plan.get("flows") or [] if f.get("hazard_m")]
        if hazard:
            label = (HAZARD_LABELS_EN if en else HAZARD_LABELS).get(hazard, hazard)
            lines.append(f"Evacuation routes avoid {label} where possible." if en else f"疏散路線已盡量繞開{label}。")
        if through:
            km = round(sum(f["hazard_m"] for f in through) / 1000, 2)
            lines.append(
                f"{len(through)} route(s) still pass through a hazard zone for {km} km in total, "
                "because there is no safer road." if en
                else f"其中 {len(through)} 條路線沒有更安全的道路，仍有部分路段經過災害範圍（合計 {km} 公里）。"
            )
    unreachable = plan.get("unreachable") or []
    if unreachable:
        names = ", ".join(display_name(n) for n in unreachable) if en else "、".join(display_name(n) for n in unreachable)
        lines.append(
            f"No safe road leads to these shelters, so they were not used as evacuation destinations: {names}." if en
            else f"以下避難所的聯外道路被封閉，沒有安全路線可到，本次未列為疏散目的地：{names}。"
        )
    cut_off = plan.get("cut_off") or []
    if cut_off:
        names = ", ".join(place_en(n) for n in cut_off) if en else "、".join(cut_off)
        lines.append(
            f"No safe road leads from these areas to any shelter, so their evacuees are counted as unplaced: {names}." if en
            else f"以下地區沒有安全道路可到任何避難所，人數計入無法安置：{names}。"
        )
    return lines


def undesignated_lines(plan: dict) -> list[str]:
    """消防署資料沒列為這類災害避難所而被排除的（全部列出：只給數字的話模型會自己補名字）"""
    names = (plan or {}).get("undesignated") or []
    if not names:
        return []
    joined = "、".join(display_name(n) for n in names)
    return [f"以下 {len(names)} 間避難所的適用災害不含{plan.get('designation')}，本次未列為疏散目的地：{joined}。"]


# 疏散分配依哪一種距離決定「近者優先」（evacuation.plan_evacuation 的 distance_basis）
BASIS_TEXT = {
    "road": "依{mode}時間由短到長",
    "mixed": "依{mode}時間由短到長，少數算不出道路路線的用直線距離估算",
    "straight": "道路路線無法計算，依直線距離估算的{mode}時間由短到長",
}
MODE_TEXT = {"walk": "步行", "drive": "開車"}


def evacuation_lines(plan: dict) -> list[str]:
    """
    後端算好的疏散分配 → prompt 文字。數字跟地圖動畫、資料庫是同一份。
    每一間都要列出來，不能截斷：實測只列前 10 間、再寫「其餘 4 間合計 650 人」時，
    模型會自己編出 4 個名稱湊數（花蓮的模擬裡冒出宜蘭國小、台東女中）。
    受影響的避難所最多也就幾十間，全列出來的成本很低
    """
    assigned = (plan or {}).get("assigned") or {}
    if not assigned:
        return hazard_lines(plan)
    ranked = sorted(assigned.items(), key=lambda kv: (-kv[1], kv[0]))
    basis = BASIS_TEXT.get(plan.get("distance_basis"), "依直線距離由近到遠").format(
        mode=MODE_TEXT.get(plan.get("mode"), "步行"),
    )
    lines = [f"疏散分配（共 {len(ranked)} 間，{basis}，並守住各避難所的剩餘空位）："]
    lines += [f"- {display_name(name)}：分配 {people} 人" for name, people in ranked]
    if plan.get("unplaced"):
        lines.append(f"範圍內避難所已全部額滿，仍有 {plan['unplaced']} 人無法安置。")
    lines += hazard_lines(plan)
    return lines


def simulation_summary(sim: dict) -> str:
    """附在模擬路線 prompt 後面的模擬摘要（災害、範圍、人口、分配）"""
    sim_type = DISASTER_TYPE_LABELS.get(sim.get("type", ""), sim.get("type", ""))
    summary = (
        f"災害類型：{sim_type}，"
        f"中心座標：({sim.get('lat')}, {sim.get('lon')})，"
        f"影響半徑：{sim.get('radius_km')} 公里，"
        f"受影響避難所數量：{sim.get('impacted_count', 0)} 個。"
    )
    extra = population_lines(sim.get("population") or {}) + evacuation_lines(sim.get("evacuation") or {})
    if extra:
        summary += "\n" + "\n".join(extra)
    return summary


def impacted_context(sim: dict) -> str:
    """問模擬結果時的資料：災害、人口、每一間受影響避難所的收容狀況與是否列為疏散目的地"""
    impacted = sim.get("impacted_shelters", [])
    sim_type = DISASTER_TYPE_LABELS.get(sim.get("type", ""), sim.get("type", ""))

    lines = [
        f"災害類型：{sim_type}",
        f"影響半徑：{sim.get('radius_km', '')} 公里",
    ]
    lines.extend(population_lines(sim.get("population") or {}))
    # 被排除的那間仍在受影響清單裡、也還有空位；不標出來，模型會把它當成可以去的地方
    plan = sim.get("evacuation") or {}
    excluded = plan.get("excluded") or {}
    near = plan.get("near_hazard") or {}
    not_designated = set(plan.get("undesignated") or [])
    label = HAZARD_LABELS.get(plan.get("hazard"), "災害潛勢區域")
    lines.append(f"受影響避難所共 {len(impacted)} 個：")
    for i, s in enumerate(impacted, 1):
        if s.name in excluded:
            note = f"（位於{label}內，本次未列為疏散目的地）"
        elif s.name in not_designated:
            note = f"（適用災害不含{plan.get('designation')}，本次未列為疏散目的地）"
        elif s.name in near:
            note = f"（{near_hazard_note(near[s.name], label)}，仍列為疏散目的地，豪雨期間請留意）"
        else:
            note = ""
        lines.append(
            f"{i}. {display_name(s.name)}：容量 {s.capacity} 人，"
            f"目前收容 {s.current_people} 人，剩餘空間 {s.remaining} 人{note}"
        )
    return "\n".join(lines)
