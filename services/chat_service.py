import os
import re
import requests
from services.vector_store import VectorStore

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://ollama:11434")

SYSTEM_PROMPT = """你是台灣東部災害避難所管理系統的 AI 決策助手。
你只能使用繁體中文回答，嚴格禁止使用任何英文單字、簡體中文或其他語言。
你只能根據提供的資料回答，不可以自行推測或編造資訊。
如果資料中沒有相關資訊，請說「目前沒有相關資料」。
回答要簡潔、務實，重點放在疏散建議與避難所資訊。
不要使用任何符號裝飾，不要使用 emoji。"""

# 觸發地理搜尋的關鍵字
GEO_KEYWORDS = ["最近", "附近", "離我最近", "最靠近", "距離最近", "哪裡最近", "近的"]

# 觸發容量排序查詢的關鍵字
CAPACITY_KEYWORDS = ["容量最大", "最多人", "容納最多", "最大容量", "哪個最大", "最大的避難所", "容量最高"]

# 觸發模擬結果查詢的關鍵字
SIMULATION_KEYWORDS = ["哪些受影響", "受影響的避難所", "哪些避難所受", "模擬結果", "影響範圍", "受災避難所", "哪些被影響"]

# 地區關鍵字對應
REGION_MAP = {
    "宜蘭": "YILAN",
    "花蓮": "HUALIEN",
    "台東": "TAITUNG",
    "臺東": "TAITUNG",
}

class ChatService:
    def __init__(self, vector_store: VectorStore, repo=None):
        self.vector_store = vector_store
        self.repo = repo
        # 儲存最新模擬結果，由 app.py 注入
        self.latest_simulation: dict = {}

    def set_simulation(self, simulation: dict):
        #由 app.py 在每次模擬後呼叫，更新最新模擬結果
        self.latest_simulation = simulation

    def _is_geo_query(self, message: str) -> bool:
        return any(kw in message for kw in GEO_KEYWORDS)

    def _is_capacity_query(self, message: str) -> bool:
        return any(kw in message for kw in CAPACITY_KEYWORDS)

    def _is_simulation_query(self, message: str) -> bool:
        return any(kw in message for kw in SIMULATION_KEYWORDS)

    def _get_simulation_context(self) -> str:
        
        #直接從 latest_simulation 取得受影響避難所清單不走 RAG 確保答案精確
        
        if not self.latest_simulation:
            return "目前尚未執行任何災害模擬。"

        sim = self.latest_simulation
        impacted = sim.get("impacted_shelters", [])

        if not impacted:
            return "目前模擬範圍內沒有受影響的避難所。"

        type_map = {"earthquake": "強震", "flood": "淹水", "fire": "火災"}
        sim_type = type_map.get(sim.get("type", ""), sim.get("type", ""))

        lines = [
            f"災害類型：{sim_type}",
            f"影響半徑：{sim.get('radius_km', '')} 公里",
            f"受影響避難所共 {len(impacted)} 個：",
        ]
        for i, s in enumerate(impacted, 1):
            lines.append(f"{i}. {s['name']}（容量 {s['capacity']} 人）")

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

            shelters.sort(key=lambda s: s.total_vessel, reverse=True)
            top = shelters[:5]

            region_label = ""
            for word, tag in REGION_MAP.items():
                if region == tag:
                    region_label = f"{word}地區"
                    break

            lines = [f"{'全東部區域' if not region_label else region_label}容量排名（由大到小）："]
            for i, s in enumerate(top, 1):
                remaining = s.total_vessel - s.total_people
                lines.append(
                    f"{i}. {s.name}：容量 {s.total_vessel} 人，"
                    f"剩餘空間 {remaining} 人"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"容量查詢失敗：{e}"

    def _extract_coords(self, message: str):
        patterns = [
            r'緯度[：:＝=\s]*([\d.]+)[,，/\s]+經度[：:＝=\s]*([\d.]+)',
            r'([\d.]+)[/,，\s]+([\d.]+)',
        ]
        for pat in patterns:
            m = re.search(pat, message)
            if m:
                try:
                    lat, lon = float(m.group(1)), float(m.group(2))
                    if 20 <= lat <= 26 and 119 <= lon <= 123:
                        return lat, lon
                except:
                    pass
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
                    f"{i}. {s['name']}：距離 {s['distance_km']} 公里，"
                    f"容量 {s['capacity']} 人，剩餘空間 {s['remaining']} 人"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"地理查詢失敗：{e}"

    def chat(
        self,
        user_message: str,
        simulation_context: str = "",
        live_feed_context: str = "",
    ) -> str:
        """
        接收使用者問題，根據問題類型選擇對應查詢方式：
        - 模擬結果查詢 → 直接讀 latest_simulation（最精確）
        - 容量排序查詢 → 直接排序資料庫
        - 地理距離查詢 → PostGIS ST_Distance
        - 一般語意查詢 → ChromaDB RAG

        live_feed_context：由 app 傳入之即時公開災害摘要（氣象署／RSS），附加於
        「即時公開災害資訊摘要」區塊，與 /api/disaster_feed 及前端輪詢通報對齊。
        """
        # 優先判斷模擬結果查詢
        if self._is_simulation_query(user_message):
            shelter_context = self._get_simulation_context()

        # 容量排序查詢
        elif self._is_capacity_query(user_message):
            shelter_context = self._get_capacity_context(user_message)

        # 地理距離查詢
        elif self._is_geo_query(user_message):
            coords = self._extract_coords(user_message)
            if coords is None:
                return "請提供您的座標以便查詢最近的避難所。例如：緯度 23.99 經度 121.60"
            lat, lon = coords
            shelter_context = self._get_nearest_context(lat, lon)

        # 一般語意查詢
        else:
            shelter_context = self.vector_store.search(user_message)

        # 組合完整 prompt context
        full_context = f"【避難所資料】\n{shelter_context}"
        if simulation_context:
            full_context += f"\n\n【目前災害模擬結果】\n{simulation_context}"
        # 【擴充】與 disaster_feed_service 摘要銜接，供 Ollama 在回答時參考（非官方推播保證）。
        if live_feed_context:
            full_context += f"\n\n【即時公開災害資訊摘要】\n{live_feed_context}"

        prompt = f"""{full_context}

【使用者問題】
{user_message}

注意：請用繁體中文回答，不得使用任何英文。"""

        try:
            response = requests.post(
                f"{OLLAMA_HOST}/api/generate",
                json={
                    "model": "llama3.2:3b",
                    "system": SYSTEM_PROMPT,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 300
                    }
                },
                timeout=120
            )
            result = response.json()
            return result.get("response", "無法取得回應").strip()

        except Exception as e:
            return f"AI 服務連線失敗：{e}"