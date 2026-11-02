"""
把災害模擬的結果寫成給使用者看的文字。模擬追問（每間分配幾人、還有多少人沒安置、哪些受影響、
哪些被排除、有什麼建議）都由這裡的程式回答，不交給模型。

以前這些是交給 llama3.2:3b 的資料，2026-11-02 的模擬追問評測（evals/run_simulation_eval.py）量到：
名稱與數字的配對沒有出錯，但長清單會在一半被截斷（63 間只列到 26 間）、一題要 34～108 秒，
問「有沒有避難所被排除」時 5 題錯 4 題（把有分配到人的 26 間說成被排除、自己編排除原因）。

人口估算、疏散分配、被排除或靠近潛勢範圍的避難所、路線繞開了什麼，數字都跟地圖動畫、資料庫是同一份
"""
from services.chat.shelter_names import display_name
from services.simulation.evacuation import HAZARD_LABELS
from services.web.i18n import HAZARD_EN, place_en

DISASTER_TYPE_LABELS = {"earthquake": "強震", "flood": "淹水", "fire": "火災"}
DISASTER_TYPE_LABELS_EN = {"earthquake": "earthquake", "flood": "flood", "fire": "fire"}
HAZARD_LABELS_EN = {"debris_flow": "a debris-flow impact zone"}

# 疏散分配用哪一種時間（evacuation.plan_evacuation 的 distance_basis）；分配本身讓所有人的時間加總最短
BASIS_TEXT = {
    "road": "讓所有人的{mode}時間加總最短",
    "mixed": "讓所有人的{mode}時間加總最短，少數算不出道路路線的用直線距離估算",
    "straight": "道路路線無法計算，依直線距離估算{mode}時間，讓加總最短",
}
BASIS_TEXT_EN = {
    "road": "shortest total time {mode} for everyone",
    "mixed": "shortest total time {mode} for everyone; a few pairs without a road route are estimated from straight-line distance",
    "straight": "road routes could not be computed, so time {mode} is estimated from straight-line distance and minimised in total",
}
MODE_TEXT = {"walk": "步行", "drive": "開車"}
MODE_TEXT_EN = {"walk": "on foot", "drive": "by car"}

# 沒有列為疏散目的地的幾種原因（hazard_notes 的 kind）；問「有沒有被排除」時只回這幾種
EXCLUSION_KINDS = ("excluded", "undesignated", "unreachable", "cut_off")


# ── 人口 ─────────────────────────────────────────

def format_people(n) -> str:
    """大數字用「萬」表示：175000 → 約 17.5 萬人"""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "不明"
    if n >= 10000:
        return f"約 {n / 10000:.1f} 萬人"
    return f"約 {n} 人"


def format_people_en(n) -> str:
    try:
        return f"about {int(n):,} people"
    except (TypeError, ValueError):
        return "unknown"


def population_lines(population: dict) -> list[str]:
    """模擬的人口估算：涵蓋人口、需疏散、可安置與缺口"""
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
        # 否則總計會跟逐筆避難所的剩餘空間加總對不上
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


def population_lines_en(population: dict) -> list[str]:
    """population_lines 的英文版"""
    if not population:
        return []
    covered = population.get("townships") or []
    lines = []
    if population.get("fallback_estimate"):
        lines.append("No major settlement lies in the affected area (mostly sea or uninhabited mountains), "
                     "so the number of evacuees is a minimum estimate based on area.")
    else:
        top = ", ".join(place_en(t["name"]) for t in covered[:4])
        lines.append(f"Population in the affected area: {format_people_en(population.get('covered_population'))}"
                     + (f" (mainly {top})" if top else "") + ".")
    ratio = population.get("evacuation_ratio")
    ratio_text = f" ({ratio * 100:.0f}% for this type of disaster)" if isinstance(ratio, (int, float)) else ""
    lines.append(f"Estimated evacuees: {format_people_en(population.get('estimated_evacuees'))}{ratio_text}.")
    if "total_remaining" in population:
        initial = population.get("initial_remaining", population.get("total_remaining"))
        lines.append(f"At the time of the simulation the shelters in range had {format_people_en(initial)} of free space, "
                     f"enough to place {format_people_en(population.get('placeable'))}.")
        shortfall = population.get("shortfall") or 0
        lines.append(
            f"Shortfall: {format_people_en(shortfall)}; shelters outside the area or extra capacity are needed." if shortfall > 0
            else "The shelters in range can take every evacuee."
        )
    return lines


# ── 潛勢範圍、排除與路線的說明 ─────────────────────────────

def near_hazard_note(info: dict, label: str) -> str:
    return f"距{label} {'、'.join(info['refs'])} 約 {info['distance_m']} 公尺"


def hazard_notes(plan: dict, lang: str = "zh") -> list[tuple[str, str]]:
    """
    [(kind, 句子)]：位在潛勢範圍內而被排除（excluded）、靠近範圍但照常分配（near）、
    不適用這類災害（undesignated），以及路線的說明（見 route_notes）。kind 讓呼叫端挑出其中幾種
    """
    plan = plan or {}
    en = lang == "en"
    excluded = plan.get("excluded") or {}
    near = plan.get("near_hazard") or {}
    notes = []
    if en:
        label = HAZARD_LABELS_EN.get(plan.get("hazard"), "a hazard zone")
        if excluded:
            names = ", ".join(f"{display_name(name)} ({', '.join(refs)})" for name, refs in sorted(excluded.items()))
            notes.append(("excluded", f"These shelters are inside {label} and were not used as evacuation destinations: {names}."))
        if near:
            names = ", ".join(
                f"{display_name(name)} ({', '.join(info['refs'])}, about {info['distance_m']} m away)"
                for name, info in sorted(near.items())
            )
            notes.append(("near", f"These shelters are near {label} and are still used; take care during heavy rain: {names}."))
        undesignated = plan.get("undesignated") or []
        if undesignated:
            joined = ", ".join(display_name(n) for n in undesignated)
            designation = HAZARD_EN.get(plan.get("designation"), plan.get("designation") or "")
            count = len(undesignated)
            notes.append(("undesignated",
                          f"{'This shelter is' if count == 1 else f'These {count} shelters are'} not designated for {designation} "
                          f"and {'was' if count == 1 else 'were'} not used as an evacuation destination: {joined}."))
        return notes + route_notes(plan, "en")
    label = HAZARD_LABELS.get(plan.get("hazard"), "災害潛勢區域")
    if excluded:
        names = "、".join(f"{display_name(name)}（{'、'.join(refs)}）" for name, refs in sorted(excluded.items()))
        notes.append(("excluded", f"以下避難所位於{label}內，本次未列為疏散目的地：{names}。"))
    if near:
        names = "、".join(
            f"{display_name(name)}（{near_hazard_note(info, label)}）" for name, info in sorted(near.items())
        )
        notes.append(("near", f"以下避難所靠近{label}，仍列為疏散目的地，豪雨期間請留意：{names}。"))
    undesignated = plan.get("undesignated") or []
    if undesignated:
        # 全部列出：只給數字的話模型會自己補名字
        joined = "、".join(display_name(n) for n in undesignated)
        notes.append(("undesignated",
                      f"以下 {len(undesignated)} 間避難所的適用災害不含{plan.get('designation')}，本次未列為疏散目的地：{joined}。"))
    return notes + route_notes(plan)


def hazard_lines(plan: dict, lang: str = "zh") -> list[str]:
    """附在模擬回答最後的說明：潛勢範圍、排除、路線"""
    return [line for _, line in hazard_notes(plan, lang)]


def route_notes(plan: dict, lang: str = "zh") -> list[tuple[str, str]]:
    """路線繞開了什麼（alerts / detour）、哪些避難所到不了（unreachable）、哪些地區沒有安全道路（cut_off）"""
    plan = plan or {}
    en = lang == "en"
    notes = []
    alerts = plan.get("alerts") or []
    reds = sum(1 for a in alerts if a.get("level") == "red")
    yellows = len(alerts) - reds
    if alerts:
        notes.append(("alerts",
                      f"Debris-flow alerts in effect: {reds} red (roads inside are closed) and {yellows} yellow "
                      "(roads inside are avoided where possible); evacuation routes account for them." if en
                      else f"目前有 {reds} 條潛勢溪流發布土石流紅色警戒（範圍內道路封閉）、"
                      f"{yellows} 條黃色警戒（範圍內道路盡量繞行），疏散路線已納入。"))
    hazard = plan.get("route_hazard")
    if hazard or alerts:
        through = [f for f in plan.get("flows") or [] if f.get("hazard_m")]
        if hazard:
            label = (HAZARD_LABELS_EN if en else HAZARD_LABELS).get(hazard, hazard)
            notes.append(("detour", f"Evacuation routes avoid {label} where possible." if en else f"疏散路線已盡量繞開{label}。"))
        if through:
            km = round(sum(f["hazard_m"] for f in through) / 1000, 2)
            notes.append(("detour",
                          f"{len(through)} route(s) still pass through a hazard zone for {km} km in total, "
                          "because there is no safer road." if en
                          else f"其中 {len(through)} 條路線沒有更安全的道路，仍有部分路段經過災害範圍（合計 {km} 公里）。"))
    unreachable = plan.get("unreachable") or []
    if unreachable:
        names = ", ".join(display_name(n) for n in unreachable) if en else "、".join(display_name(n) for n in unreachable)
        notes.append(("unreachable",
                      f"No safe road leads to these shelters, so they were not used as evacuation destinations: {names}." if en
                      else f"以下避難所的聯外道路被封閉，沒有安全路線可到，本次未列為疏散目的地：{names}。"))
    cut_off = plan.get("cut_off") or []
    if cut_off:
        names = ", ".join(place_en(n) for n in cut_off) if en else "、".join(cut_off)
        notes.append(("cut_off",
                      f"No safe road leads from these areas to any shelter, so their evacuees are counted as unplaced: {names}." if en
                      else f"以下地區沒有安全道路可到任何避難所，人數計入無法安置：{names}。"))
    return notes


def route_lines(plan: dict, lang: str = "zh") -> list[str]:
    return [line for _, line in route_notes(plan, lang)]


# ── 模擬追問的回答 ─────────────────────────────────────────

def allocation_lines(plan: dict, lang: str = "zh") -> list[str]:
    """
    每一間分到幾人，全部列出、不截斷。以前這是交給模型的資料：只列前 10 間再寫「其餘 4 間合計 650 人」時，
    模型會自己編出 4 個名稱湊數；整份交給它照抄，長清單又會在一半被截斷
    """
    en = lang == "en"
    plan = plan or {}
    assigned = plan.get("assigned") or {}
    if not assigned:
        return ["No shelter was assigned any evacuees." if en else "這次沒有任何避難所分配到疏散人員。"]
    ranked = sorted(assigned.items(), key=lambda kv: (-kv[1], kv[0]))
    if en:
        basis = BASIS_TEXT_EN.get(plan.get("distance_basis"), "nearest in a straight line first").format(
            mode=MODE_TEXT_EN.get(plan.get("mode"), "on foot"),
        )
        lines = [f"Evacuation plan ({len(ranked)} shelters, {basis}, never over a shelter's free space):"]
        lines += [f"- {display_name(name)}: {people} people" for name, people in ranked]
        if plan.get("unplaced"):
            lines.append(f"Every reachable shelter in range is full; {plan['unplaced']} people could not be placed.")
        return lines
    basis = BASIS_TEXT.get(plan.get("distance_basis"), "依直線距離由近到遠").format(
        mode=MODE_TEXT.get(plan.get("mode"), "步行"),
    )
    lines = [f"疏散分配（共 {len(ranked)} 間，{basis}，並守住各避難所的剩餘空位）："]
    lines += [f"- {display_name(name)}：分配 {people} 人" for name, people in ranked]
    if plan.get("unplaced"):
        lines.append(f"範圍內避難所已全部額滿，仍有 {plan['unplaced']} 人無法安置。")
    return lines


def unplaced_lines(sim: dict, lang: str = "zh") -> list[str]:
    """需疏散、已安置、無法安置的人數，以及無法安置的人怎麼辦"""
    plan = sim.get("evacuation") or {}
    placed, unplaced = int(plan.get("placed") or 0), int(plan.get("unplaced") or 0)
    shelters = len(plan.get("assigned") or {})
    if lang == "en":
        return [
            f"{placed + unplaced} people need to evacuate: {placed} are placed in {shelters} shelters "
            f"and {unplaced} could not be placed.",
            "Shelters outside the area or temporary shelters are needed for them." if unplaced
            else "The shelters in range can take every evacuee.",
        ]
    return [
        f"需疏散 {placed + unplaced} 人：已安置 {placed} 人（分配到 {shelters} 間避難所），仍有 {unplaced} 人無法安置。",
        "無法安置的人需要調度範圍外的避難所，或開設臨時收容處所。" if unplaced else "範圍內避難所足以安置全部疏散人口。",
    ]


def _not_destinations(plan: dict) -> dict[str, str]:
    """沒有列為疏散目的地的避難所 → 原因（excluded / undesignated / unreachable）"""
    reasons = dict.fromkeys(plan.get("unreachable") or [], "unreachable")
    reasons |= dict.fromkeys(plan.get("undesignated") or [], "undesignated")
    reasons |= dict.fromkeys(plan.get("excluded") or {}, "excluded")
    return reasons


def impacted_lines(sim: dict, lang: str = "zh") -> list[str]:
    """每一間受影響避難所的收容狀況，並標出沒有列為疏散目的地的（不標的話它看起來還有空位可去）"""
    en = lang == "en"
    impacted = sim.get("impacted_shelters", [])
    plan = sim.get("evacuation") or {}
    reasons = _not_destinations(plan)
    near = plan.get("near_hazard") or {}
    designation = plan.get("designation") or ""
    if en:
        label = HAZARD_LABELS_EN.get(plan.get("hazard"), "a hazard zone")
        notes = {
            "excluded": f" (inside {label}; not used as a destination)",
            "undesignated": f" (not designated for {HAZARD_EN.get(designation, designation)}; not used as a destination)",
            "unreachable": " (no safe road; not used as a destination)",
        }
        lines = [f"{len(impacted)} shelters are affected:"]
        for i, s in enumerate(impacted, 1):
            note = notes.get(reasons.get(s.name), "")
            if not note and s.name in near:
                info = near[s.name]
                note = f" (about {info['distance_m']} m from {', '.join(info['refs'])}; still used, take care during heavy rain)"
            lines.append(f"{i}. {display_name(s.name)}: capacity {s.capacity}, sheltering {s.current_people}, "
                         f"{s.remaining} spaces left{note}")
        return lines
    label = HAZARD_LABELS.get(plan.get("hazard"), "災害潛勢區域")
    notes = {
        "excluded": f"（位於{label}內，本次未列為疏散目的地）",
        "undesignated": f"（適用災害不含{designation}，本次未列為疏散目的地）",
        "unreachable": "（聯外道路被封閉，本次未列為疏散目的地）",
    }
    lines = [f"受影響避難所共 {len(impacted)} 個："]
    for i, s in enumerate(impacted, 1):
        note = notes.get(reasons.get(s.name), "")
        if not note and s.name in near:
            note = f"（{near_hazard_note(near[s.name], label)}，仍列為疏散目的地，豪雨期間請留意）"
        lines.append(f"{i}. {display_name(s.name)}：容量 {s.capacity} 人，"
                     f"目前收容 {s.current_people} 人，剩餘空間 {s.remaining} 人{note}")
    return lines


def exclusion_lines(sim: dict, lang: str = "zh") -> list[str]:
    """哪些避難所沒有列為疏散目的地、為什麼；一間都沒有時要明說，不能讓人以為漏講"""
    lines = [line for kind, line in hazard_notes(sim.get("evacuation") or {}, lang) if kind in EXCLUSION_KINDS]
    if lines:
        return lines
    count = len(sim.get("impacted_shelters", []))
    return [f"No shelter was excluded in this simulation; all {count} affected shelters were considered as destinations."
            if lang == "en" else f"這次模擬沒有任何避難所被排除，受影響的 {count} 間都列入疏散分配的考慮。"]


def advice_lines(sim: dict, lang: str = "zh") -> list[str]:
    """
    「有什麼建議」：由程式依數字給的摘要，不讓模型自由發揮。模型的建議依據也是同一份數字，
    自由發揮只會多出錯的機會（評測裡它把有分配到人的避難所說成被排除）
    """
    en = lang == "en"
    plan = sim.get("evacuation") or {}
    reasons = _not_destinations(plan)
    unplaced = int(plan.get("unplaced") or 0)
    with_room = sorted(
        (s for s in sim.get("impacted_shelters", []) if s.name not in reasons and s.remaining > 0),
        key=lambda s: (-s.remaining, s.name),
    )
    busiest = sorted((plan.get("assigned") or {}).items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    lines = []
    if en:
        if unplaced:
            lines.append(f"- Every reachable shelter in range is full and {unplaced} people still need a place: "
                         "bring in shelters outside the area or open temporary shelters.")
        elif with_room:
            top = ", ".join(f"{display_name(s.name)} ({s.remaining})" for s in with_room[:3])
            lines.append(f"- The shelters in range can take everyone; {sum(s.remaining for s in with_room)} spaces "
                         f"are still free, most at {top}.")
        if busiest:
            top = ", ".join(f"{display_name(n)} ({p} people)" for n, p in busiest)
            lines.append(f"- The most evacuees go to {top}; send supplies and staff there first.")
        lines.append("- For the full plan, ask 「每間避難所分配了多少人」; for excluded shelters, ask 「有沒有避難所被排除」.")
        return lines
    if unplaced:
        lines.append(f"- 範圍內可前往的避難所已全部額滿，仍有 {unplaced} 人需要安置：請調度範圍外的避難所，或開設臨時收容處所。")
    elif with_room:
        top = "、".join(f"{display_name(s.name)}（{s.remaining} 人）" for s in with_room[:3])
        lines.append(f"- 範圍內避難所足以安置全部疏散人口，目前還有 {sum(s.remaining for s in with_room)} 個空位，最多的是 {top}。")
    if busiest:
        top = "、".join(f"{display_name(n)}（{p} 人）" for n, p in busiest)
        lines.append(f"- 疏散人數最多的是 {top}，物資與人力可以優先安排到這幾處。")
    lines.append("- 要看完整分配可以問「每間避難所分配了多少人」，要看被排除的避難所可以問「有沒有避難所被排除」。")
    return lines


def simulation_answer(sim: dict, topics: list[str], lang: str = "zh") -> str:
    """
    模擬追問的回答，全部由程式組出來。topics 是 chat.intent.simulation_topics 的結果；
    空的時候（「有什麼建議」「模擬結果」）回人口、安置與建議的摘要。
    潛勢範圍、排除、路線的說明一律附在最後，前面已經講過的不重複
    """
    en = lang == "en"
    plan = sim.get("evacuation") or {}
    sim_type = sim.get("type", "")
    count = sim.get("impacted_count", len(sim.get("impacted_shelters", [])))
    head = (f"Simulation: {DISASTER_TYPE_LABELS_EN.get(sim_type, sim_type)}, {sim.get('radius_km')} km radius, "
            f"{count} shelters affected." if en
            else f"模擬：{DISASTER_TYPE_LABELS.get(sim_type, sim_type)}，半徑 {sim.get('radius_km')} 公里，受影響避難所 {count} 間。")
    sections = []
    if not topics:
        population = (population_lines_en if en else population_lines)(sim.get("population") or {})
        sections.append(population + unplaced_lines(sim, lang)[:1])
        sections.append(["Suggestions:" if en else "建議："] + advice_lines(sim, lang))
    builders = {
        "allocation": lambda: allocation_lines(plan, lang),
        "unplaced": lambda: unplaced_lines(sim, lang),
        "impacted": lambda: impacted_lines(sim, lang),
        "excluded": lambda: exclusion_lines(sim, lang),
    }
    sections += [builders[topic]() for topic in topics]
    said = {line for section in sections for line in section}
    notes = [line for line in hazard_lines(plan, lang) if line not in said]
    if notes:
        sections.append(notes)
    return "\n\n".join([head] + ["\n".join(section) for section in sections])
