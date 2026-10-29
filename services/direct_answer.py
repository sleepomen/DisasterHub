"""
程式直接排好的避難所回答：清單、容量排名、概況、點名、篩選落空。

這些題目的答案本來就是結構化資料（名稱、鄉鎮、容量、剩餘空位），以前也是把同樣的資料交給
llama3.2:3b 逐字重抄一遍：清單題 p50 約 40 秒，而且會抄錯——生成評測裡台東市的避難所被標成「宜蘭」，
點名題答完那一間又自己往下湊出不存在的「宜蘭社區活動中心 30 人」。
防災知識題改成直接給原文之後從 11 秒變成不到 1 秒、編造歸零（見 services/knowledge.py），這裡是同一個做法。

只處理規則層已經決定好「要哪些避難所」的情況；沒有任何條件的純語意題（「知本附近」）
top-k 裡混著不相干的，仍交給模型挑。

英文模式（lang="en"）：避難所名稱與地址保留中文（資料沒有英文，現場招牌也是中文），其餘用英文
"""
from services.i18n import FACILITY_EN, HAZARD_EN, place_en, shelters_en
from services.shelter_profile import HAZARD_KEYS, strip_region_tag


def _capacity_text(meta: dict, lang: str) -> str:
    capacity = int(meta.get("capacity") or 0)
    remaining = int(meta.get("remaining") or 0)
    if capacity <= 0:
        # 來源填 0：寫「容量 0 人、剩餘 0 人」會被讀成已經額滿
        return "capacity not provided" if lang == "en" else "收容人數資料未提供"
    if lang == "en":
        return f"capacity {capacity}, {remaining} spaces left"
    return f"容量 {capacity} 人，剩餘空位 {remaining} 人"


def _place(meta: dict, lang: str) -> str:
    place = meta.get("township") or meta.get("county") or ""
    return place_en(place) if lang == "en" else place


def shelter_line(meta: dict, lang: str = "zh") -> str:
    """清單裡的一行：「名稱：鄉鎮，容量 N 人，剩餘空位 M 人」"""
    name = strip_region_tag(meta["name"])
    place = _place(meta, lang)
    if lang == "en":
        return f"{name}: {place + ', ' if place else ''}{_capacity_text(meta, lang)}"
    return f"{name}：{place + '，' if place else ''}{_capacity_text(meta, lang)}"


def shelter_detail(meta: dict, lang: str = "zh") -> str:
    """點名題：那一間的地址、收容狀況與消防署標註的適用災害"""
    en = lang == "en"
    lines = [strip_region_tag(meta["name"])]
    address = meta.get("address")
    if address:
        lines.append(f"- Address: {address}" if en else f"- 地址：{address}")
    capacity = int(meta.get("capacity") or 0)
    current = int(meta.get("current_people") or 0)
    remaining = int(meta.get("remaining") or 0)
    if capacity > 0:
        lines.append(
            f"- Capacity {capacity}, currently sheltering {current}, {remaining} spaces left" if en
            else f"- 容量 {capacity} 人，目前收容 {current} 人，剩餘空位 {remaining} 人"
        )
    else:
        lines.append("- Capacity not provided" if en else "- 收容人數資料未提供")
    hazards = [label for label, key in HAZARD_KEYS.items() if meta.get(key)]
    extras = []
    if hazards:
        extras.append(
            f"designated for {', '.join(HAZARD_EN[h] for h in hazards)}" if en else f"適用災害：{'、'.join(hazards)}"
        )
    if meta.get("indoor"):
        extras.append("indoor" if en else "室內")
    if meta.get("vulnerable_ok"):
        extras.append("suitable for vulnerable evacuees" if en else "適合避難弱者")
    if extras:
        lines.append(f"- {'; '.join(extras)}" if en else f"- {'；'.join(extras)}")
    return "\n".join(lines)


def _bullets(metas: list[dict], lang: str, numbered: bool = False) -> str:
    if numbered:
        return "\n".join(f"{i}. {shelter_line(m, lang)}" for i, m in enumerate(metas, 1))
    return "\n".join(f"- {shelter_line(m, lang)}" for m in metas)


def compose(plan, result, lang: str = "zh") -> str | None:
    """
    依檢索結果的形狀排出回答；回 None 代表這一題交給模型（沒有篩選條件的純語意題）。
    plan 是 query_rules.QueryPlan，result 是 vector_store.PlannedResult
    """
    metas = [h.metadata for h in result.hits]
    if not metas:
        return None
    if lang == "en":
        return _compose_en(plan, result, metas)
    condition = plan.describe()
    shown = len(metas)

    if result.named:
        return "\n\n".join(shelter_detail(m) for m in metas)

    if result.fell_back:
        return (
            f"沒有找到符合「{condition}」條件的避難所。以下是最接近的 {shown} 筆，僅供參考：\n"
            + _bullets(metas, lang)
        )

    if result.ranked:
        order = "由小到大" if plan.capacity_order == "asc" else "由大到小"
        scope = f"符合「{condition}」的避難所" if result.filtered else "東部三縣的避難所"
        count = f"共 {result.total} 筆，列出前 {shown} 筆" if result.total > shown else f"共 {shown} 筆"
        return f"{scope}依容量{order}排序（{count}）：\n" + _bullets(metas, lang, numbered=True)

    if result.overview:
        o = result.overview
        unknown = f"（另有 {o['unknown_capacity']} 筆未提供收容人數）" if o["unknown_capacity"] else ""
        groups = "、".join(f"{label} {n} 間" for label, n in o["groups"])
        top = o["groups"][0][0] if o["groups"] else ""
        example = f"{top}有哪些避難所" if o["group_by"] == "鄉鎮" else f"{condition}的{top}"
        return (
            f"符合「{condition}」的避難所共 {result.total} 筆，容量合計 {o['capacity']} 人，"
            f"目前剩餘空間 {o['remaining']} 人{unknown}。\n"
            f"依{o['group_by']}：{groups}。\n\n"
            f"最相關的 {shown} 筆：\n{_bullets(metas, lang)}\n\n"
            f"數量較多，沒有逐筆列出。可以再指定鄉鎮或設施類型，例如「{example}」，查看完整清單。"
        )

    if result.filtered:
        if result.total > shown:
            head = f"符合「{condition}」的避難所共 {result.total} 筆，以下列出最相關的 {shown} 筆："
        else:
            head = f"符合「{condition}」的避難所共 {shown} 筆："
        return f"{head}\n{_bullets(metas, lang)}"

    return None


def _compose_en(plan, result, metas: list[dict]) -> str | None:
    condition = plan.describe("en")
    shown = len(metas)

    if result.named:
        return "\n\n".join(shelter_detail(m, "en") for m in metas)

    if result.fell_back:
        lead = "Here is the closest one" if shown == 1 else f"These are the {shown} closest"
        return f"No shelter matches {condition}. {lead}, for reference only:\n" + _bullets(metas, "en")

    if result.ranked:
        order = "smallest first" if plan.capacity_order == "asc" else "largest first"
        scope = f"Shelters matching {condition}" if result.filtered else "Shelters in Yilan, Hualien and Taitung"
        count = f"{result.total} in total, top {shown} shown" if result.total > shown else f"{shown} in total"
        return f"{scope}, by capacity, {order} ({count}):\n" + _bullets(metas, "en", numbered=True)

    if result.overview:
        o = result.overview
        by_township = o["group_by"] == "鄉鎮"
        unknown = f" ({o['unknown_capacity']} more without a stated capacity)" if o["unknown_capacity"] else ""
        label = place_en if by_township else (lambda f: FACILITY_EN.get(f, f))
        groups = ", ".join(f"{label(name)} {n}" for name, n in o["groups"])
        top = o["groups"][0][0] if o["groups"] else ""
        # 範例問句給中文：第一階段英文模式只翻回答，問題仍是中文最準，給英文範例反而查不到
        example = f"{top}有哪些避難所" if by_township else f"{plan.describe()}的{top}"
        return (
            f"{shelters_en(result.total)} match {condition}, with a total capacity of {o['capacity']} "
            f"and {o['remaining']} spaces currently free{unknown}.\n"
            f"By {'township' if by_township else 'facility type'}: {groups}.\n\n"
            f"The {shown} most relevant:\n{_bullets(metas, 'en')}\n\n"
            f"There are too many to list. To see the full list, ask about one township or facility type, "
            f"for example 「{example}」."
        )

    if result.filtered:
        if result.total > shown:
            head = f"{shelters_en(result.total)} match {condition}; the {shown} most relevant:"
        else:
            head = f"{shelters_en(shown)} {'matches' if shown == 1 else 'match'} {condition}:"
        return f"{head}\n{_bullets(metas, 'en')}"

    return None
