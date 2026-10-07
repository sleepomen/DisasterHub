import copy
import json
import logging
import re
import threading
import time

import requests

import config
from services import query_rules
from services.metrics import metrics
from services.shelter_profile import strip_region_tag
from services.vector_store import NO_DATA, NO_MATCH, VectorStore

logger = logging.getLogger(__name__)

# 生成評測（evals/run_gen_eval.py）發現小模型最常見的錯誤是「資料就在眼前卻回沒有資料」，
# 所以這裡把「資料是系統篩好的、直接用」講得很明確，拒答只留給資料區真的空白或問題與避難所無關的情況
SYSTEM_PROMPT = """你是台灣東部（宜蘭、花蓮、台東）災害避難所管理系統的決策助手。
規則：
1. 只能使用繁體中文，不可以出現任何英文單字、簡體中文或 emoji，也不要用符號裝飾。
2. 只能根據【避難所資料】回答，不可以推測或編造。資料裡的名稱與數字要原樣引用，不可改寫或自行計算。
3. 【避難所資料】是系統依照問題篩選出來的相關資料。只要裡面列有避難所，就直接用它回答，不可以說沒有資料。
4. 只有在【避難所資料】明確寫著沒有資料，或問題與避難所完全無關（例如天氣、補助申請）時，才回答「目前沒有相關資料」。
5. 回答要簡潔務實，重點放在避難所名稱、地點、容量與剩餘空位。
6. 不要自我介紹、不要寒暄、不要反問，直接回答。"""

# 檢索層判定沒有相關資料（索引空的、或最接近的文件也離得太遠）時的固定回覆，不必打 LLM
NO_RELEVANT_REPLY = "目前沒有相關資料。本系統只回答宜蘭、花蓮、台東三縣的避難所資訊，請改問避難所的位置、容量或剩餘空位。"

# 觸發地理搜尋的關鍵字
GEO_KEYWORDS = ["最近", "附近", "離我最近", "最靠近", "距離最近", "哪裡最近", "近的"]

# 地理問句去掉關鍵字與這些泛用詞之後還有東西（地名、路名、鄉鎮），就代表使用者已經說了地點，
# 該走檢索而不是回頭要座標；「離我最近的避難所」去完就空了，才需要座標
GEO_GENERIC_WORDS = [
    "避難所", "收容所", "收容", "空間", "空位", "還有", "有沒有", "有什麼", "有哪些", "哪些", "哪裡", "哪間", "哪個",
    "在哪", "可以", "能", "去", "到", "的", "嗎", "呢", "我", "這裡", "這邊", "那邊", "學校", "地方", "請問", "想", "要",
    "找", "查", "一下", "是", "有", "？", "?", "，", "。", " ",
]

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

# 這三句本身就是答案，不是檢索結果。以前它們被當成【避難所資料】塞進 prompt，
# 等於多花一次生成讓模型把一句話照抄一遍，還給了它在空資料上編造的機會
NOT_SIMULATED_REPLY = (
    "目前尚未執行災害模擬，沒有受影響的避難所可以回報。"
    "請先在左側設定模擬中心、災害類型與受災半徑並執行空間模擬。"
)
NO_IMPACTED_REPLY = "目前模擬範圍內沒有受影響的避難所。可以擴大受災半徑重新模擬，或改問其他地區的避難所。"
NO_NEARBY_REPLY = "附近沒有找到避難所資料。本系統只涵蓋宜蘭、花蓮、台東三縣，請確認座標是否落在這三個縣內。"

# 問句被導到哪條路。線上分布跟評測題庫的分布不會一樣，而「使用者實際問什麼」
# 只有這裡量得到：例如 needs_coords 偏高就表示很多人問「最近的避難所」卻沒有座標可用
ROUTE_COUNTERS = (
    "chat.route.out_of_scope",
    "chat.route.simulation",
    "chat.route.nearest",
    "chat.route.needs_coords",
    "chat.route.no_simulation_advice",
    "chat.route.rag",
    "chat.route.context_error",
)
# 答案從哪裡來，以及生成的結局
OUTCOME_COUNTERS = (
    "chat.answer.early_reply",
    "chat.answer.generated",
    "chat.generation.ok",
    "chat.generation.interrupted",
    "chat.generation.unavailable",
    "chat.generation.empty",
)
metrics.register(*ROUTE_COUNTERS, *OUTCOME_COUNTERS)

DISASTER_TYPE_LABELS = {"earthquake": "強震", "flood": "淹水", "fire": "火災"}

COORD_PATTERNS = [
    re.compile(r"緯度[：:＝=\s]*(\d{2}\.\d+)[,，/\s]*經度[：:＝=\s]*(\d{3}\.\d+)"),
    re.compile(r"經度[：:＝=\s]*(\d{3}\.\d+)[,，/\s]*緯度[：:＝=\s]*(\d{2}\.\d+)"),
    re.compile(r"(?<![\d.])(2\d\.\d+)[,，/\s]+(1\d{2}\.\d+)(?![\d.])"),
]
GENERIC_ERROR = "查詢避難所資料時發生錯誤，請稍後再試。"
AI_UNAVAILABLE = "AI 服務目前無法使用，請稍後再試。"
# 串流中途斷掉：前面已經吐出來的文字留著，後面補一句讓使用者知道答案沒講完
STREAM_INTERRUPTED = "（回答中斷，請稍後再試。）"


class GenerationTimeout(RuntimeError):
    """整段生成超過 OLLAMA_TIMEOUT，串流已中止"""


# 顯示用名稱就是去掉 [REGION] 前綴。直接沿用索引端那一個函式，
# 不要再維護第二份同樣的正則（原本這個 pattern 在兩個模組各定義一次，而且寫法還不一樣）
display_name = strip_region_tag


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


class ChatService:
    def __init__(self, vector_store: VectorStore, repo=None):
        self.vector_store = vector_store
        self.repo = repo
        # 模擬快照是全域共用狀態，會被請求執行緒同時讀寫：
        # 寫入一律持鎖，讀取一律拿深拷貝，就不會讀到改到一半的清單
        self._lock = threading.Lock()
        self.latest_simulation: dict = {}

    def set_simulation(self, simulation: dict):
        with self._lock:
            self.latest_simulation = simulation

    def clear_simulation(self):
        with self._lock:
            self.latest_simulation = {}

    def _snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.latest_simulation)

    def has_simulation(self) -> bool:
        with self._lock:
            return bool(self.latest_simulation)

    def refresh_occupancy(self, shelters) -> None:
        """
        模擬回寫佔用數後，把 latest_simulation 裡快照的受影響清單同步成資料庫最新值，
        「哪些受影響」這類直接讀快照的問題才不會回答舊數字。
        """
        lookup = {s.name: s for s in shelters}
        with self._lock:
            if not self.latest_simulation:
                return
            # 快照存的是 Shelter 物件，所以直接換成資料庫最新的那一個，
            # 不必逐欄位覆寫（以前是 dict，改一個欄位就要記得同步 remaining）
            impacted = [lookup.get(s.name, s) for s in self.latest_simulation.get("impacted_shelters", [])]
            self.latest_simulation["impacted_shelters"] = impacted

            # 人口摘要的總剩餘空間也要跟著逐筆數字走；模擬當下的值另外留一份，
            # 可安置 / 缺口這些規劃數字才有基準可以對照
            population = self.latest_simulation.get("population")
            if population and "total_remaining" in population:
                population.setdefault("initial_remaining", population["total_remaining"])
                current = sum(s.remaining for s in impacted)
                population["total_remaining"] = current
                population["placed"] = max(0, int(population["initial_remaining"]) - current)

    def _is_geo_query(self, message: str) -> bool:
        return any(kw in message for kw in GEO_KEYWORDS)

    @staticmethod
    def _needs_coordinates(message: str) -> bool:
        """地理問句裡沒有任何地點線索時才需要座標"""
        rest = message
        for word in GEO_KEYWORDS + GEO_GENERIC_WORDS:
            rest = rest.replace(word, "")
        return len(rest.strip()) < 2

    def _is_explicit_simulation_query(self, message: str) -> bool:
        return any(kw in message for kw in SIMULATION_KEYWORDS)

    def _is_simulation_followup(self, message: str) -> bool:
        return self.has_simulation() and any(kw in message for kw in SIMULATION_FOLLOWUP_KEYWORDS)

    def _is_simulation_query(self, message: str) -> bool:
        return self._is_explicit_simulation_query(message) or self._is_simulation_followup(message)

    def _simulation_summary(self) -> str:
        sim = self._snapshot()
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

    def _get_simulation_context(self) -> tuple[str | None, str | None]:
        """
        回傳 (context, early_reply)，跟 build_context 同一個形狀。
        沒有模擬、或範圍內沒有受影響的避難所時，答案就是那一句固定回覆，
        不必當成資料餵給 LLM 再等它抄一遍。
        """
        sim = self._snapshot()
        if not sim:
            return None, NOT_SIMULATED_REPLY

        impacted = sim.get("impacted_shelters", [])
        if not impacted:
            return None, NO_IMPACTED_REPLY

        sim_type = DISASTER_TYPE_LABELS.get(sim.get("type", ""), sim.get("type", ""))

        lines = [
            f"災害類型：{sim_type}",
            f"影響半徑：{sim.get('radius_km', '')} 公里",
        ]
        lines.extend(population_lines(sim.get("population") or {}))
        lines.append(f"受影響避難所共 {len(impacted)} 個：")
        for i, s in enumerate(impacted, 1):
            lines.append(
                f"{i}. {display_name(s.name)}：容量 {s.capacity} 人，"
                f"目前收容 {s.current_people} 人，剩餘空間 {s.remaining} 人"
            )

        return "\n".join(lines), None

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

    def _get_nearest_context(self, lat: float, lon: float) -> tuple[str | None, str | None]:
        """回傳 (context, early_reply)。查不到或查壞了都是固定回覆，不能當成資料送進 prompt"""
        if self.repo is None:
            logger.error("地理查詢無法進行：repo 未初始化")
            return None, GENERIC_ERROR
        try:
            results = self.repo.get_nearest_shelters(lat, lon, limit=5)
            if not results:
                return None, NO_NEARBY_REPLY

            lines = [f"使用者位置：緯度 {lat}、經度 {lon}"]
            lines.append("距離最近的避難所（依距離由近到遠排序）：")
            for i, n in enumerate(results, 1):
                s = n.shelter
                lines.append(
                    f"{i}. {display_name(s.name)}：距離 {n.distance_km} 公里，"
                    f"容量 {s.capacity} 人，剩餘空間 {s.remaining} 人"
                )
            return "\n".join(lines), None
        except Exception:
            logger.exception("地理查詢失敗")
            return None, GENERIC_ERROR

    def build_context(self, user_message: str):
        """
        根據問題類型選擇對應查詢方式，回傳 (context, early_reply)。判斷順序：
        1. 範圍外縣市 → 直接拒答，不管有沒有模擬在跑
        2. 明確問模擬結果 → 直接讀模擬快照（最精確）
        3. 有座標的地理距離查詢 → PostGIS ST_Distance；沒座標也沒地名才回頭要座標，
           「知本附近」「中華路一段附近」這種有地點線索的交給檢索
        4. 模擬進行中的追問（建議 / 空間 / 缺口）→ 模擬快照
        5. 沒模擬卻要疏散建議 → 請先跑模擬
        6. 其餘 → 規則層篩選 + ChromaDB 語意檢索（容量排名也走這裡）
        """
        plan = query_rules.analyze(user_message)
        if plan.out_of_scope:
            metrics.route("out_of_scope")
            return None, query_rules.out_of_scope_reply(plan.out_of_scope)

        if self._is_explicit_simulation_query(user_message):
            metrics.route("simulation")
            return self._get_simulation_context()

        if self._is_geo_query(user_message):
            coords = self._extract_coords(user_message)
            if coords is not None:
                metrics.route("nearest")
                return self._get_nearest_context(*coords)
            if not self._is_simulation_followup(user_message) and self._needs_coordinates(user_message):
                metrics.route("needs_coords")
                return None, "請提供您的座標以便查詢最近的避難所。例如：緯度 23.99 經度 121.60"

        if self._is_simulation_followup(user_message):
            metrics.route("simulation")
            return self._get_simulation_context()

        if any(kw in user_message for kw in EVACUATION_ADVICE_KEYWORDS):
            metrics.route("no_simulation_advice")
            return None, NO_SIMULATION_REPLY

        # 查詢向量要打 Ollama embedding，Ollama 掛掉時這裡會先炸；
        # 要回跟生成失敗一樣的降級訊息，而不是讓 /api/chat 變成 500
        metrics.route("rag")
        try:
            context = self.vector_store.search(user_message, plan=plan)
        except Exception:
            logger.exception("向量檢索失敗")
            return None, AI_UNAVAILABLE
        if context in (NO_DATA, NO_MATCH):
            return None, NO_RELEVANT_REPLY
        return context, None

    def build_prompt(self, user_message: str, shelter_context: str) -> str:
        full_context = f"【避難所資料】\n{shelter_context}"
        summary = self._simulation_summary()
        if summary:
            full_context += f"\n\n【目前災害模擬結果】\n{summary}"

        # 列舉題（一個縣 20 筆）要能在 num_predict 內列完，所以規定每間一行、只講關鍵欄位
        return f"""{full_context}

【使用者問題】
{user_message}

回答要求：
1. 直接用上面的資料回答，回答時先寫出避難所名稱。問某一間避難所時只回答那一間，引用該筆的地址、容量、目前收容人數與剩餘空位。
2. 列舉多間避難所時每間一行，格式「名稱：鄉鎮，容量 N 人，剩餘空位 M 人」，把資料裡符合的全部列完，不要省略。
3. 資料裡的數字原樣引用，不要自行加總或改寫。
4. 只能用繁體中文，不得出現英文。"""

    def chat_stream(self, user_message: str):
        """
        串流回答：逐段 yield 事件 dict，type 有 delta（文字片段）與 error（固定錯誤句）。
        跟 chat() 一樣不對外拋例外，所有錯誤都轉成使用者看得懂的訊息，
        呼叫端只負責把 text 接起來或往下送。
        """
        started = time.monotonic()
        try:
            shelter_context, early_reply = self.build_context(user_message)
        except Exception:
            # 任何查詢層的例外都不該變成 500；細節只進 log，不回給使用者
            metrics.route("context_error")
            logger.exception("建立查詢內容失敗")
            self._log_summary("early_reply", "context_error", 0, len(GENERIC_ERROR), started)
            yield {"type": "error", "text": GENERIC_ERROR}
            return
        if early_reply:
            # 檢索層就答得出來（範圍外、要座標、沒有相關資料），不必打 LLM，一次給完
            metrics.incr("chat.answer.early_reply")
            self._log_summary("early_reply", "ok", 0, len(early_reply), started)
            yield {"type": "delta", "text": early_reply}
            return

        metrics.incr("chat.answer.generated")
        prompt = self.build_prompt(user_message, shelter_context)
        reply_chars = 0
        try:
            for chunk in self.generate_stream(prompt):
                reply_chars += len(chunk)
                yield {"type": "delta", "text": chunk}
        except Exception:
            # 已經吐出文字才斷掉是「中斷」，一個字都沒吐出來是「服務不可用」，兩者要分開看
            outcome = "interrupted" if reply_chars else "unavailable"
            metrics.incr("chat.generation." + outcome)
            logger.exception("Ollama 串流生成失敗")
            self._log_summary("generated", outcome, len(shelter_context), reply_chars, started)
            yield {"type": "error", "text": STREAM_INTERRUPTED if reply_chars else AI_UNAVAILABLE}
            return
        if not reply_chars:
            # 連線沒問題但模型一個字都沒給：不要讓前端收到空白泡泡
            metrics.incr("chat.generation.empty")
            logger.warning("Ollama 串流沒有產生任何文字")
            self._log_summary("generated", "empty", len(shelter_context), 0, started)
            yield {"type": "error", "text": AI_UNAVAILABLE}
            return
        metrics.incr("chat.generation.ok")
        self._log_summary("generated", "ok", len(shelter_context), reply_chars, started)

    @staticmethod
    def _log_summary(source: str, outcome: str, context_chars: int, reply_chars: int, started: float) -> None:
        """
        每次問答結束留一行 key=value 摘要，並把延遲記進取樣。
        離線評測量的是那 130 道題，這一行量的是真實流量；兩邊對不上時從這裡開始查。
        失敗的生成也要進取樣，不然 p95 會被偷偷修掉。
        """
        elapsed_ms = (time.monotonic() - started) * 1000
        metrics.observe("chat." + source, elapsed_ms)
        logger.info(
            "chat route=%s source=%s outcome=%s context_chars=%d reply_chars=%d ms=%d",
            metrics.last_route(), source, outcome, context_chars, reply_chars, round(elapsed_ms),
        )

    def chat(self, user_message: str) -> str:
        """非串流版：把 chat_stream() 的片段接起來，給不吃 SSE 的呼叫端（腳本、測試）用"""
        return "".join(event["text"] for event in self.chat_stream(user_message)).strip()

    def _payload(self, prompt: str, stream: bool) -> dict:
        """生成請求的內容。串流與非串流共用同一份，評測量到的才跟線上同一個模型設定"""
        return {
            "model": config.OLLAMA_MODEL,
            "system": SYSTEM_PROMPT,
            "prompt": prompt,
            "stream": stream,
            "options": {
                "temperature": config.OLLAMA_TEMPERATURE,
                "num_predict": config.OLLAMA_NUM_PREDICT,
                "num_ctx": config.OLLAMA_NUM_CTX,
            },
        }

    def generate(self, prompt: str) -> dict:
        """
        打 Ollama 生成，一次拿完整的 JSON（response、done_reason、eval_count …）。
        生成端評測要靠它拿 token 數與截斷原因；失敗直接拋例外由呼叫端處理。
        """
        response = requests.post(
            f"{config.OLLAMA_HOST}/api/generate",
            json=self._payload(prompt, False),
            timeout=config.OLLAMA_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()

    def generate_stream(self, prompt: str):
        """
        串流生成：逐段 yield 文字。Ollama 的 /api/generate 在 stream=True 下回 NDJSON，
        一行一個片段，最後一行帶 done。

        requests 的 timeout 在串流模式只管「單次讀取」要等多久，整段時間不受它約束，
        所以另外用 deadline 擋：一次生成仍然最長 OLLAMA_TIMEOUT 秒，app.py 的併發名額才算得準。
        超時與 Ollama 回報的錯誤都拋例外，由 chat_stream 轉成使用者訊息。
        """
        deadline = time.monotonic() + config.OLLAMA_TIMEOUT
        with requests.post(
            f"{config.OLLAMA_HOST}/api/generate",
            json=self._payload(prompt, True),
            timeout=config.OLLAMA_TIMEOUT,
            stream=True,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    logger.warning("Ollama 串流有一行無法解析，已略過")
                    continue
                if data.get("error"):
                    raise RuntimeError(f"Ollama 回報錯誤：{data['error']}")
                chunk = data.get("response") or ""
                if chunk:
                    yield chunk
                if data.get("done"):
                    return
                if time.monotonic() >= deadline:
                    raise GenerationTimeout(f"生成超過 {config.OLLAMA_TIMEOUT} 秒，已中止串流")
