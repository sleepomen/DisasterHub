import re
import logging
import requests
from services.vector_store import VectorStore
from services import query_rules
import config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是台灣東部災害避難所管理系統的 AI 決策助手。
你只能使用繁體中文回答，嚴格禁止使用任何英文單字、簡體中文或其他語言。
你只能根據提供的資料回答，不可以自行推測或編造資訊。
如果資料中沒有相關資訊，請說「目前沒有相關資料」。
回答要簡潔、務實，重點放在疏散建議與避難所資訊。
不要使用任何符號裝飾，不要使用 emoji。"""

# 觸發地理搜尋的關鍵字
GEO_KEYWORDS = ["最近", "附近", "離我最近", "最靠近", "距離最近", "哪裡最近", "近的"]

# 觸發容量排序查詢的關鍵字
CAPACITY_KEYWORDS = ["容量最大", "最多人", "容納最多", "最大容量", "哪個最大", "最大的避難所", "容量最高", "哪間最大", "容量排名", "容量排序", "由大到小", "由小到大"]

# 觸發模擬結果查詢的關鍵字
SIMULATION_KEYWORDS = ["哪些受影響", "受影響的避難所", "哪些避難所受", "模擬結果", "影響範圍", "受災避難所", "哪些被影響"]

# 有模擬進行中時，這些追問也該看模擬結果而不是全域語意檢索；
# 沒有模擬時不啟用，否則「有什麼建議」會被導去回答「尚未執行模擬」
SIMULATION_FOLLOWUP_KEYWORDS = ["疏散建議", "怎麼疏散", "如何疏散", "疏散到哪", "安置", "調度", "建議", "還有空間", "夠不夠", "缺口"]

# 明確在要疏散建議、但還沒有模擬可依據：直接請使用者先跑模擬，不要拿全域檢索硬湊
EVACUATION_ADVICE_KEYWORDS = ["疏散建議", "怎麼疏散", "如何疏散", "疏散到哪", "疏散計畫"]
NO_SIMULATION_REPLY = (
    "目前尚未執行災害模擬，無法給出疏散建議。"
    "請先在左側設定模擬中心、災害類型與受災半徑並執行空間模擬，我會依受影響避難所的容量與人口估算提供建議。"
)

# 地區關鍵字對應
REGION_MAP = {
    "宜蘭": "YILAN",
    "花蓮": "HUALIEN",
    "台東": "TAITUNG",
    "臺東": "TAITUNG",
}

DISASTER_TYPE_LABELS = {"earthquake": "強震", "flood": "淹水", "fire": "火災"}

REGION_TAG_PATTERN = re.compile(r"^\[[A-Z]+\]\s*")
COORD_PATTERNS = [
    re.compile(r"緯度[：:＝=\s]*(\d{2}\.\d+)[,，/\s]*經度[：:＝=\s]*(\d{3}\.\d+)"),
    re.compile(r"經度[：:＝=\s]*(\d{3}\.\d+)[,，/\s]*緯度[：:＝=\s]*(\d{2}\.\d+)"),
    re.compile(r"(?<![\d.])(2\d\.\d+)[,，/\s]+(1\d{2}\.\d+)(?![\d.])"),
]
GENERIC_ERROR = "查詢避難所資料時發生錯誤，請稍後再試。"
AI_UNAVAILABLE = "AI 服務目前無法使用，請稍後再試。"


def display_name(name: str) -> str:
    return REGION_TAG_PATTERN.sub("", name or "")


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
        lines.append(
            f"範圍內避難所剩餘空間合計{format_people(population.get('total_remaining'))}，"
            f"可安置{format_people(population.get('placeable'))}。"
        )
        shortfall = population.get("shortfall") or 0
        if shortfall > 0:
            lines.append(f"收容缺口{format_people(shortfall)}，需要調度範圍外的避難所或擴大收容。")
        else:
            lines.append("範圍內避難所空間足以安置全部疏散人口。")
    return lines


class ChatService:
    def __init__(self, vector_store: VectorStore, repo=None):
        self.vector_store = vector_store
        self.repo = repo
        self.latest_simulation: dict = {}

    def set_simulation(self, simulation: dict):
        self.latest_simulation = simulation

    def clear_simulation(self):
        self.latest_simulation = {}

    def refresh_occupancy(self, shelters) -> None:
        """
        模擬回寫佔用數後，把 latest_simulation 裡快照的受影響清單同步成資料庫最新值，
        「哪些受影響」這類直接讀快照的問題才不會回答舊數字。
        """
        if not self.latest_simulation:
            return
        lookup = {s.name: s for s in shelters}
        for item in self.latest_simulation.get("impacted_shelters", []):
            s = lookup.get(item.get("name"))
            if s is None:
                continue
            item["capacity"] = s.capacity
            item["current_ppl"] = s.current_people
            item["remaining"] = s.remaining

    def _is_geo_query(self, message: str) -> bool:
        return any(kw in message for kw in GEO_KEYWORDS)

    def _is_capacity_query(self, message: str) -> bool:
        return any(kw in message for kw in CAPACITY_KEYWORDS)

    def _is_simulation_query(self, message: str) -> bool:
        if any(kw in message for kw in SIMULATION_KEYWORDS):
            return True
        return bool(self.latest_simulation) and any(kw in message for kw in SIMULATION_FOLLOWUP_KEYWORDS)

    def _simulation_summary(self) -> str:
        sim = self.latest_simulation
        if not sim:
            return ""
        sim_type = DISASTER_TYPE_LABELS.get(sim.get("type", ""), sim.get("type", ""))
        summary = (
            f"災害類型：{sim_type}，"
            f"中心座標：({sim.get('lat')}, {sim.get('lon')})，"
            f"影響半徑：{sim.get('radius_km')} 公里，"
            f"受影響避難所數量：{sim.get('impacted_count', 0)} 個。"
        )
        extra = population_lines(sim.get("population") or {})
        if extra:
            summary += "\n" + "\n".join(extra)
        return summary

    def _get_simulation_context(self) -> str:
        if not self.latest_simulation:
            return "目前尚未執行任何災害模擬。"

        sim = self.latest_simulation
        impacted = sim.get("impacted_shelters", [])

        if not impacted:
            return "目前模擬範圍內沒有受影響的避難所。"

        sim_type = DISASTER_TYPE_LABELS.get(sim.get("type", ""), sim.get("type", ""))

        lines = [
            f"災害類型：{sim_type}",
            f"影響半徑：{sim.get('radius_km', '')} 公里",
        ]
        lines.extend(population_lines(sim.get("population") or {}))
        lines.append(f"受影響避難所共 {len(impacted)} 個：")
        for i, s in enumerate(impacted, 1):
            capacity = s.get("capacity", 0)
            current = s.get("current_ppl", 0)
            remaining = s.get("remaining", max(0, capacity - current))
            lines.append(
                f"{i}. {display_name(s['name'])}：容量 {capacity} 人，"
                f"目前收容 {current} 人，剩餘空間 {remaining} 人"
            )

        return "\n".join(lines)

    def _extract_region(self, message: str):
        for word, tag in REGION_MAP.items():
            if word in message:
                return tag
        return None

    def _get_capacity_context(self, message: str) -> str:
        if self.repo is None:
            return "無法取得避難所資料。"
        try:
            shelters = self.repo.get_all_shelters()
            if not shelters:
                return "目前沒有避難所資料。"

            region = self._extract_region(message)
            if region:
                shelters = [s for s in shelters if region in s.name]

            if not shelters:
                return "該地區沒有找到避難所資料。"

            shelters.sort(key=lambda s: s.capacity, reverse=True)
            top = shelters[:5]

            region_label = ""
            for word, tag in REGION_MAP.items():
                if region == tag:
                    region_label = f"{word}地區"
                    break

            lines = [f"{'全東部區域' if not region_label else region_label}容量排名（由大到小）："]
            for i, s in enumerate(top, 1):
                lines.append(
                    f"{i}. {display_name(s.name)}：容量 {s.capacity} 人，"
                    f"剩餘空間 {s.remaining} 人"
                )
            return "\n".join(lines)
        except Exception:
            logger.exception("容量查詢失敗")
            return GENERIC_ERROR

    def _extract_coords(self, message: str):
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

    def _get_nearest_context(self, lat: float, lon: float) -> str:
        if self.repo is None:
            return "無法取得避難所資料（repo 未初始化）。"
        try:
            results = self.repo.get_nearest_shelters(lat, lon, limit=5)
            if not results:
                return "附近沒有找到避難所資料。"

            lines = [f"使用者位置：緯度 {lat}、經度 {lon}"]
            lines.append("距離最近的避難所（依距離由近到遠排序）：")
            for i, s in enumerate(results, 1):
                lines.append(
                    f"{i}. {display_name(s['name'])}：距離 {s['distance_km']} 公里，"
                    f"容量 {s['capacity']} 人，剩餘空間 {s['remaining']} 人"
                )
            return "\n".join(lines)
        except Exception:
            logger.exception("地理查詢失敗")
            return GENERIC_ERROR

    def build_context(self, user_message: str):
        """
        根據問題類型選擇對應查詢方式，回傳 (context, early_reply)：
        - 模擬結果查詢 → 直接讀 latest_simulation（最精確）
        - 地理距離查詢 → PostGIS ST_Distance
        - 容量排序查詢 → 直接排序資料庫
        - 一般語意查詢 → ChromaDB RAG
        """
        if self._is_simulation_query(user_message):
            return self._get_simulation_context(), None

        if not self.latest_simulation and any(kw in user_message for kw in EVACUATION_ADVICE_KEYWORDS):
            return None, NO_SIMULATION_REPLY

        if self._is_geo_query(user_message):
            coords = self._extract_coords(user_message)
            if coords is None:
                return None, "請提供您的座標以便查詢最近的避難所。例如：緯度 23.99 經度 121.60"
            lat, lon = coords
            return self._get_nearest_context(lat, lon), None

        if self._is_capacity_query(user_message):
            return self._get_capacity_context(user_message), None

        # 規則層：範圍外縣市直接拒答；地區 / 鄉鎮 / 設施 / 容量條件轉成 metadata 篩選
        plan = query_rules.analyze(user_message)
        if plan.out_of_scope:
            return None, query_rules.out_of_scope_reply(plan.out_of_scope)
        return self.vector_store.search(user_message, plan=plan), None

    def build_prompt(self, user_message: str, shelter_context: str) -> str:
        full_context = f"【避難所資料】\n{shelter_context}"
        summary = self._simulation_summary()
        if summary:
            full_context += f"\n\n【目前災害模擬結果】\n{summary}"

        return f"""{full_context}

【使用者問題】
{user_message}

注意：
1. 如果沒有相關資料或語意不符就說 沒有相關資料。
2. 請用繁體中文回答，不得使用任何英文。"""

    def chat(self, user_message: str) -> str:
        shelter_context, early_reply = self.build_context(user_message)
        if early_reply:
            return early_reply

        prompt = self.build_prompt(user_message, shelter_context)

        try:
            response = requests.post(
                f"{config.OLLAMA_HOST}/api/generate",
                json={
                    "model": config.OLLAMA_MODEL,
                    "system": SYSTEM_PROMPT,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": config.OLLAMA_TEMPERATURE,
                        "num_predict": config.OLLAMA_NUM_PREDICT
                    }
                },
                timeout=config.OLLAMA_TIMEOUT
            )
            response.raise_for_status()
            result = response.json()
            return result.get("response", AI_UNAVAILABLE).strip()

        except Exception:
            logger.exception("Ollama 呼叫失敗")
            return AI_UNAVAILABLE
