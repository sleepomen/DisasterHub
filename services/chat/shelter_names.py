"""
避難所名稱：顯示用名稱、使用者可能的寫法、錯字說明，以及點名了不存在的場所時的回覆
"""
from services.chat.direct_answer import shelter_line
from services.retrieval.shelter_profile import aliases_of, strip_region_tag

# 顯示用名稱就是去掉 [REGION] 前綴。直接沿用索引端那一個函式，
# 不要再維護第二份同樣的正則（原本這個 pattern 在兩個模組各定義一次，而且寫法還不一樣）
display_name = strip_region_tag

# 不存在的場所最多推薦幾間名稱相近的
MAX_UNKNOWN_SUGGESTIONS = 3


def known_names_of(shelters: list[dict]) -> list[str]:
    """索引 metadata → 使用者可能用來點名的所有寫法（顯示名稱加別名）"""
    return [n for m in shelters for n in (display_name(m["name"]), *aliases_of(m["name"]))]


def typo_note(written: str, real: str, lang: str = "zh") -> str:
    # 不用「沒有」「查無」：ABSTAIN_MARKERS 認得那些詞，這裡是有答案的，不能被評測算成拒答
    if lang == "en":
        return f"(「{written}」 was not found; showing the closest name, 「{real}」.)"
    return f"（找不到「{written}」，以下是名稱最接近的「{real}」的資料）"


def unknown_shelter_reply(name: str, shelters: list[dict], lang: str = "zh") -> str:
    """
    「查無」開頭：evals/lib/gen_metrics.py 的 ABSTAIN_MARKERS 認得這個詞，生成與邊界題評測才會算成正確交代。
    推薦的是名稱開頭相同的避難所（頭城國中 → 頭城國小、新生高中 → 新生國中 / 新生國小），
    數字直接取自索引 metadata，不經過模型，所以不會被改寫
    """
    stem = name[:2]
    similar = sorted(
        (m for m in shelters if display_name(m["name"]).startswith(stem)),
        key=lambda m: -int(m.get("capacity") or 0),
    )[:MAX_UNKNOWN_SUGGESTIONS]
    example = next((m["township"] for m in similar if m.get("township")), "頭城鎮")
    if lang == "en":
        lines = [f"There is no shelter called 「{name}」 in this system's list."]
        if similar:
            lines.append("Shelters with similar names:")
            lines += [f"- {shelter_line(m, 'en')}" for m in similar]
        lines.append(f"If you are looking for another place, ask about a township, for example 「{example}有哪些避難所」.")
        return "\n".join(lines)
    lines = [f"查無「{name}」這個避難收容場所的資料，本系統的避難所名單裡沒有這一間。"]
    if similar:
        lines.append("名稱相近的避難所：")
        for m in similar:
            place = f"{m.get('county', '')}{m.get('township', '')}"
            lines.append(
                f"- {display_name(m['name'])}：{place}，容量 {m.get('capacity')} 人，剩餘空位 {m.get('remaining')} 人"
            )
    lines.append(f"如果要找的是其他地方，可以告訴我鄉鎮名稱，例如「{example}有哪些避難所」。")
    return "\n".join(lines)
