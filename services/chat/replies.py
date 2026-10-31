"""
聊天的固定回覆：不必打模型、由程式直接回給使用者的句子，以及它們的英文版。

這些句子本身就是答案（要座標、沒有相關資料、還沒模擬、服務掛了），
build_context 內部一律產生中文，回傳前才用 tr() 翻成英文
"""

# 知識路由的固定回覆。開頭用「目前沒有相關資料」：evals/lib/gen_metrics.py 的 ABSTAIN_MARKERS 認得
NO_KNOWLEDGE_REPLY = (
    "目前沒有相關資料：防災知識資料裡沒有這個問題的說明。"
    "本系統的防災知識涵蓋颱風、地震、土石流、海嘯、豪雨與淹水的應變方式與警戒等級；"
    "實際的警戒與撤離，請以中央氣象署、農村水保署與地方政府的公告為準。"
)
KNOWLEDGE_UNAVAILABLE = "防災知識資料目前無法使用，請稍後再試。"

# 檢索層判定沒有相關資料（索引空的、或最接近的文件也離得太遠）時的固定回覆，不必打 LLM
NO_RELEVANT_REPLY = "目前沒有相關資料。本系統只回答宜蘭、花蓮、台東三縣的避難所資訊，請改問避難所的位置、容量或剩餘空位。"

# 明確在要疏散建議、但還沒有模擬可依據（services/chat/intent.py 的 EVACUATION_ADVICE_KEYWORDS）
NO_SIMULATION_REPLY = (
    "目前尚未執行災害模擬，無法給出疏散建議。"
    "請先在左側設定模擬中心、災害類型與受災半徑並執行空間模擬，我會依受影響避難所的容量與人口估算提供建議。"
)

# 這三句本身就是答案，不是檢索結果。以前它們被當成【避難所資料】塞進 prompt，
# 等於多花一次生成讓模型把一句話照抄一遍，還給了它在空資料上編造的機會
NOT_SIMULATED_REPLY = (
    "目前尚未執行災害模擬，沒有受影響的避難所可以回報。"
    "請先在左側設定模擬中心、災害類型與受災半徑並執行空間模擬。"
)
NO_IMPACTED_REPLY = "目前模擬範圍內沒有受影響的避難所。可以擴大受災半徑重新模擬，或改問其他地區的避難所。"
NO_NEARBY_REPLY = "附近沒有找到避難所資料。本系統只涵蓋宜蘭、花蓮、台東三縣，請確認座標是否落在這三個縣內。"
NEEDS_COORDS_REPLY = "請提供您的座標以便查詢最近的避難所。例如：緯度 23.99 經度 121.60"
GENERIC_ERROR = "查詢避難所資料時發生錯誤，請稍後再試。"
AI_UNAVAILABLE = "AI 服務目前無法使用，請稍後再試。"
# 串流中途斷掉：前面已經吐出來的文字留著，後面補一句讓使用者知道答案沒講完
STREAM_INTERRUPTED = "（回答中斷，請稍後再試。）"


# 英文模式的固定回覆（services/web/i18n.py 的第一階段：回答用英文，問題仍以中文最準）。
# 用中文句子當 key：build_context 內部一律產生中文固定句，回傳前才翻，
# 「混合題遇到沒有相關資料」這類比對才不必分兩種語言寫
EN_REPLIES = {
    NO_KNOWLEDGE_REPLY: (
        "No relevant information: the disaster-preparedness guidance does not cover this question. "
        "It covers what to do in typhoons, earthquakes, debris flows, tsunamis, heavy rain and floods, and what the "
        "alert levels mean. For actual alerts and evacuation orders, follow the Central Weather Administration, "
        "the Agency of Rural Development and Soil and Water Conservation, and your local government."
    ),
    KNOWLEDGE_UNAVAILABLE: "The disaster-preparedness guidance is unavailable at the moment. Please try again later.",
    NO_RELEVANT_REPLY: (
        "No relevant information. This system only answers questions about shelters in Yilan, Hualien and Taitung; "
        "please ask about a shelter's location, capacity or free spaces."
    ),
    NO_SIMULATION_REPLY: (
        "No disaster simulation has been run yet, so there is no evacuation advice to give. Set the simulation centre, "
        "disaster type and radius on the left and run the simulation; advice will be based on the affected shelters' "
        "capacity and the population estimate."
    ),
    NOT_SIMULATED_REPLY: (
        "No disaster simulation has been run yet, so there are no affected shelters to report. Set the simulation "
        "centre, disaster type and radius on the left and run the simulation."
    ),
    NO_IMPACTED_REPLY: (
        "No shelters are affected within the simulated area. Try a larger radius, or ask about shelters elsewhere."
    ),
    NO_NEARBY_REPLY: (
        "No shelters were found nearby. This system only covers Yilan, Hualien and Taitung; please check that the "
        "coordinates are inside these three counties."
    ),
    NEEDS_COORDS_REPLY: (
        "Please give your coordinates to find the nearest shelter, for example: 緯度 23.99 經度 121.60 "
        "(latitude 23.99, longitude 121.60)."
    ),
    GENERIC_ERROR: "Something went wrong while looking up shelter data. Please try again later.",
    AI_UNAVAILABLE: "The AI service is unavailable at the moment. Please try again later.",
    STREAM_INTERRUPTED: "(The answer was interrupted. Please try again later.)",
}


def tr(text: str | None, lang: str) -> str | None:
    """固定回覆翻成英文；不是固定句（已經是該語言的動態回覆）就原樣回傳"""
    if lang != "en" or text is None:
        return text
    return EN_REPLIES.get(text, text)
