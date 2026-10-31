"""
問句要走哪條路：地理（最近／附近）、模擬結果、模擬中的追問、疏散建議，以及從問句裡抓座標。

只看問句本身的字面；需要系統狀態的判斷（有沒有模擬在跑）由 ChatService 自己加。
地名、設施、容量這類條件在 services/chat/query_rules.py
"""
import re

from services.web.metrics import metrics

# 觸發地理搜尋的關鍵字
GEO_KEYWORDS = ["最近", "附近", "離我最近", "最靠近", "距離最近", "哪裡最近", "近的"]

# 地理問句去掉關鍵字與這些泛用詞之後還有東西（地名、路名、鄉鎮），就代表使用者已經說了地點，
# 該走檢索而不是回頭要座標；「離我最近的避難所」去完就空了，才需要座標
# 這張表是減法啟發式的黑名單：剝不掉的詞會被當成地名。路由評測
# （evals/run_route_eval.py）抓到「離我最近的收容所是哪一間」剝完剩下「離哪一間」，
# 於是被誤判成有地名而走了 RAG。這類漏字只能逐個補，是這個做法的結構性弱點：
# 真正的修法是改成正面判斷（query_rules 的鄉鎮／路名抽取），但那會漏掉
# 「知本」這種不在任何清單裡的地名，所以目前仍用這張表＋評測當護欄
GEO_GENERIC_WORDS = [
    "避難所", "收容所", "收容", "空間", "空位", "還有", "有沒有", "有什麼", "有哪些", "哪些", "哪裡", "哪間", "哪個",
    "哪一間", "哪一個", "在哪", "可以", "能", "去", "到", "離", "的", "嗎", "呢", "我", "這裡", "這邊", "那邊",
    "學校", "地方", "請問", "想", "要", "找", "查", "一下", "是", "有", "？", "?", "，", "。", " ",
]

# 觸發模擬結果查詢的關鍵字
SIMULATION_KEYWORDS = ["哪些受影響", "受影響的避難所", "哪些避難所受", "模擬結果", "影響範圍", "受災避難所", "哪些被影響"]

# 有模擬進行中時，這些追問也該看模擬結果而不是全域語意檢索；
# 沒有模擬時不啟用，否則「有什麼建議」會被導去回答「尚未執行模擬」
# 「分配」「排除」「模擬」：實測「這次模擬每間避難所分配了多少人？有沒有避難所被排除？」走了全域檢索，
# 模型同時看到 10 間不相干的避難所與模擬結果，答出「宜蘭國小：無避難所被排除」
SIMULATION_FOLLOWUP_KEYWORDS = [
    "疏散建議", "怎麼疏散", "如何疏散", "疏散到哪", "安置", "調度", "建議", "還有空間", "夠不夠", "缺口",
    "分配", "排除", "模擬",
]

# 明確在要疏散建議、但還沒有模擬可依據：直接請使用者先跑模擬，不要拿全域檢索硬湊
EVACUATION_ADVICE_KEYWORDS = ["疏散建議", "怎麼疏散", "如何疏散", "疏散到哪", "疏散計畫"]

COORD_PATTERNS = [
    re.compile(r"緯度[：:＝=\s]*(\d{2}\.\d+)[,，/\s]*經度[：:＝=\s]*(\d{3}\.\d+)"),
    re.compile(r"經度[：:＝=\s]*(\d{3}\.\d+)[,，/\s]*緯度[：:＝=\s]*(\d{2}\.\d+)"),
    re.compile(r"(?<![\d.])(2\d\.\d+)[,，/\s]+(1\d{2}\.\d+)(?![\d.])"),
]

# 問句被導到哪條路。線上分布跟評測題庫的分布不會一樣，而「使用者實際問什麼」
# 只有這裡量得到：例如 needs_coords 偏高就表示很多人問「最近的避難所」卻沒有座標可用
ROUTE_COUNTERS = (
    "chat.route.out_of_scope",
    "chat.route.off_topic",
    "chat.route.simulation",
    "chat.route.nearest",
    "chat.route.needs_coords",
    "chat.route.no_simulation_advice",
    "chat.route.unknown_name",
    "chat.route.no_clue",
    "chat.route.rag",
    # 防災知識：只有知識、知識加避難所
    "chat.route.knowledge",
    "chat.route.knowledge_mixed",
    "chat.route.context_error",
)
metrics.register(*ROUTE_COUNTERS)


def is_geo_query(message: str) -> bool:
    return any(kw in message for kw in GEO_KEYWORDS)


def needs_coordinates(message: str) -> bool:
    """地理問句裡沒有任何地點線索時才需要座標"""
    rest = message
    for word in GEO_KEYWORDS + GEO_GENERIC_WORDS:
        rest = rest.replace(word, "")
    return len(rest.strip()) < 2


def is_explicit_simulation_query(message: str) -> bool:
    return any(kw in message for kw in SIMULATION_KEYWORDS)


def mentions_simulation_followup(message: str) -> bool:
    """模擬追問的字眼；有沒有模擬在跑由呼叫端判斷"""
    return any(kw in message for kw in SIMULATION_FOLLOWUP_KEYWORDS)


def asks_evacuation_advice(message: str) -> bool:
    return any(kw in message for kw in EVACUATION_ADVICE_KEYWORDS)


def extract_coords(message: str):
    """問句裡的 (緯度, 經度)，要落在台灣附近；沒有回 None"""
    for i, pat in enumerate(COORD_PATTERNS):
        m = pat.search(message)
        if not m:
            continue
        try:
            a, b = float(m.group(1)), float(m.group(2))
        except ValueError:
            continue
        lat, lon = (b, a) if i == 1 else (a, b)
        if 20 <= lat <= 26 and 119 <= lon <= 123:
            return lat, lon
    return None
