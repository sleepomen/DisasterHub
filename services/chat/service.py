"""
聊天服務：依問題類型組出資料（context）或直接回答（early_reply），需要時交給模型生成，並做回答防護。

拆出去的部分：
- services/chat/intent.py      問句要走哪條路、抓座標
- services/chat/replies.py     固定回覆與英文翻譯
- services/simulation/text.py  模擬結果寫成文字
- services/chat/shelter_names.py    避難所名稱、錯字、查無此間
- services/chat/llm_client.py       呼叫 Ollama
- services/retrieval/knowledge.py        防災知識段落的組字
"""
import copy
import logging
import threading
import time

import config
from services.chat import intent, llm_client, query_rules
from services.chat.answer_guard import LineGuard, filter_lines, supported_names
from services.chat.replies import (
    AI_UNAVAILABLE,
    GENERIC_ERROR,
    KNOWLEDGE_UNAVAILABLE,
    NEEDS_COORDS_REPLY,
    NO_IMPACTED_REPLY,
    NO_KNOWLEDGE_REPLY,
    NO_NEARBY_REPLY,
    NO_RELEVANT_REPLY,
    NO_SIMULATION_REPLY,
    NOT_SIMULATED_REPLY,
    STREAM_INTERRUPTED,
    tr,
)
from services.chat.shelter_names import display_name, known_names_of, typo_note, unknown_shelter_reply
from services.geo.routing import nearest_by_road
from services.retrieval.knowledge import (
    KNOWLEDGE_HEADER,
    KNOWLEDGE_MARK,
    SHELTER_HEADER,
    KnowledgeChunk,
    KnowledgeHit,
    KnowledgeStore,
    knowledge_answer,
    knowledge_context,
    knowledge_text,
    shelter_part,
    sources_text,
)
from services.retrieval.shelter_profile import aliases_of
from services.retrieval.vector_store import NO_DATA, NO_MATCH, VectorStore
from services.simulation.evacuation import HAZARD_LABELS, HAZARD_WARN_M, split_hazard_hits
from services.simulation.text import hazard_lines, impacted_context, near_hazard_note, simulation_summary
from services.web.metrics import metrics

logger = logging.getLogger(__name__)

# 答案從哪裡來，以及生成的結局
OUTCOME_COUNTERS = (
    "chat.answer.early_reply",
    # early_reply 裡由程式排好的避難所清單／點名回答（不是拒答或固定句）
    "chat.answer.direct",
    "chat.answer.generated",
    "chat.generation.ok",
    # 回答防護拿掉的行數（提到 prompt 以外的避難所）
    "chat.guard.dropped_lines",
    "chat.generation.interrupted",
    "chat.generation.unavailable",
    "chat.generation.empty",
)
metrics.register(*OUTCOME_COUNTERS)


class ChatService:
    def __init__(self, vector_store: VectorStore, repo=None, knowledge: KnowledgeStore | None = None):
        self.vector_store = vector_store
        self.repo = repo
        # 沒有給（測試、評測腳本）時知識題回固定的「無法使用」，其他路由照常
        self.knowledge = knowledge
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

    def _is_simulation_followup(self, message: str) -> bool:
        return self.has_simulation() and intent.mentions_simulation_followup(message)

    def _is_simulation_query(self, message: str) -> bool:
        return intent.is_explicit_simulation_query(message) or self._is_simulation_followup(message)

    def _simulation_summary(self) -> str:
        sim = self._snapshot()
        return simulation_summary(sim) if sim else ""

    def _get_simulation_context(self) -> tuple[str | None, str | None]:
        """
        回傳 (context, early_reply)，跟 build_context 同一個形狀。
        沒有模擬、或範圍內沒有受影響的避難所時，答案就是那一句固定回覆，
        不必當成資料餵給 LLM 再等它抄一遍。
        """
        sim = self._snapshot()
        if not sim:
            return None, NOT_SIMULATED_REPLY
        if not sim.get("impacted_shelters"):
            return None, NO_IMPACTED_REPLY
        return impacted_context(sim), None

    def _debris_flow_shelters(self) -> tuple[dict[str, list[str]], dict[str, dict]]:
        """
        (位在土石流潛勢溪流影響範圍內的, 範圍外 HAZARD_WARN_M 公尺內的)。
        查不到就當作沒有，不讓最近避難所查詢跟著失敗
        """
        try:
            return split_hazard_hits(self.repo.shelter_hazard_distances("debris_flow", HAZARD_WARN_M))
        except Exception:
            logger.exception("查詢土石流潛勢區域失敗")
            return {}, {}

    def _get_nearest_context(self, lat: float, lon: float) -> tuple[str | None, str | None]:
        """回傳 (context, early_reply)。查不到或查壞了都是固定回覆，不能當成資料送進 prompt"""
        if self.repo is None:
            logger.error("地理查詢無法進行：repo 未初始化")
            return None, GENERIC_ERROR
        try:
            results = nearest_by_road(self.repo, lat, lon, limit=5)
            if not results:
                return None, NO_NEARBY_REPLY

            inside, near = self._debris_flow_shelters()
            label = HAZARD_LABELS["debris_flow"]
            lines = [f"使用者位置：緯度 {lat}、經度 {lon}"]
            if any(n.road_km is not None for n in results):
                lines.append("距離最近的避難所（依道路距離由近到遠排序，括號內是直線距離）：")
            else:
                lines.append("距離最近的避難所（道路距離無法計算，依直線距離由近到遠排序）：")
            for i, n in enumerate(results, 1):
                s = n.shelter
                if s.name in inside:
                    warning = f"（注意：位於{label} {'、'.join(inside[s.name])} 內，豪雨時不宜前往）"
                elif s.name in near:
                    warning = f"（注意：{near_hazard_note(near[s.name], label)}，豪雨期間請留意）"
                else:
                    warning = ""
                if n.unreachable:
                    distance = f"直線距離 {n.distance_km} 公里，但目前沒有安全道路可到（土石流紅色警戒範圍內的道路封閉）"
                elif n.road_km is not None:
                    distance = f"道路距離 {n.road_km} 公里（直線 {n.distance_km} 公里）"
                    if n.walk_min is not None:
                        distance += f"，步行約 {n.walk_min} 分鐘、開車約 {n.drive_min} 分鐘"
                    if n.hazard_m:
                        distance += f"，路線有 {n.hazard_m} 公尺經過土石流警戒範圍，沒有更安全的路"
                else:
                    distance = f"直線距離 {n.distance_km} 公里"
                lines.append(
                    f"{i}. {display_name(s.name)}：{distance}，"
                    f"容量 {s.capacity} 人，剩餘空間 {s.remaining} 人{warning}"
                )
            return "\n".join(lines), None
        except Exception:
            logger.exception("地理查詢失敗")
            return None, GENERIC_ERROR

    def build_context(self, user_message: str, lang: str = "zh"):
        """
        回傳 (context, early_reply)。lang="en" 時 early_reply 是英文（固定句在這裡統一翻譯，
        清單、知識原文、查無這些帶資料的回覆在產生時就用英文）；context 永遠是中文，那是給模型的資料
        """
        context, early_reply = self._build_context(user_message, lang)
        return context, tr(early_reply, lang)

    def _build_context(self, user_message: str, lang: str):
        """
        根據問題類型選擇對應查詢方式，回傳 (context, early_reply)。判斷順序：
        1. 範圍外縣市 → 直接拒答，不管有沒有模擬在跑
        2. 明確問模擬結果 → 直接讀模擬快照（最精確）
        3. 有座標的地理距離查詢 → PostGIS ST_Distance；沒座標也沒地名才回頭要座標，
           「知本附近」「中華路一段附近」這種有地點線索的交給檢索
        4. 模擬進行中的追問（建議 / 空間 / 缺口）→ 模擬快照
        5. 沒模擬卻要疏散建議 → 請先跑模擬
        6. 其餘 → 規則層篩選 + ChromaDB 語意檢索（容量排名也走這裡）

        在這些之前先看是不是防災知識題（「颱風前要準備什麼」「黃色警戒是什麼意思」）：
        只問知識的直接回知識文件原文（early_reply，不打模型）；同時要避難所的，
        context 前面放知識段落、後面放避難所資料，由 chat_stream 先送原文再讓模型列避難所。
        模擬相關的問題優先走模擬，「模擬中怎麼疏散」要的是這次的分配，不是一般須知
        """
        knowledge = query_rules.knowledge_request(user_message)
        if knowledge is None or self._is_simulation_query(user_message):
            return self._shelter_context(user_message, lang)

        if not knowledge.mixed:
            metrics.route("knowledge")
            hits = self._knowledge_hits(knowledge.search_text, knowledge.hazards)
            if hits is None:
                return None, KNOWLEDGE_UNAVAILABLE
            if not hits:
                return None, NO_KNOWLEDGE_REPLY
            return None, knowledge_answer([h.chunk for h in hits], lang)

        hits = self._knowledge_hits(knowledge.search_text, knowledge.hazards)
        if not hits:
            # 知識那邊沒東西就跟沒有這條路由一樣
            return self._shelter_context(user_message, lang)
        # 避難所那一側只拿問避難所的那半句去查，行為才跟單獨問一樣：
        # 「地震了在家要怎麼辦，附近收容所還有空位嗎」整句去查不會要座標，評測裡避難所資料是空的，
        # 模型就編出「宜蘭縣宜蘭市中正路123號」整串假避難所
        context, early_reply = self._shelter_context(query_rules.shelter_clauses(user_message), lang)
        metrics.route("knowledge_mixed")
        chunks = [h.chunk for h in hits]
        if context is None or not context.strip():
            # 避難所那邊是固定回覆（要座標、範圍外、沒有相關資料）或資料是空的：不打模型，
            # 知識原文照樣給，固定回覆接在後面（「沒有相關資料」那句只會讓人以為知識也沒有，不接）
            note = tr(early_reply, lang) if early_reply and early_reply != NO_RELEVANT_REPLY else ""
            parts = [knowledge_text(chunks, lang), note, sources_text(chunks, lang)]
            return None, "\n\n".join(part for part in parts if part)
        return f"{knowledge_context(hits)}\n\n{SHELTER_HEADER}\n{context}", None

    def _knowledge_hits(self, user_message: str, hazards: list[str]) -> list[KnowledgeHit] | None:
        """距離門檻以內的知識段落；知識庫沒載入或查詢失敗回 None"""
        if self.knowledge is None or not self.knowledge.ensure_ready():
            return None
        try:
            hits = self.knowledge.search(user_message, hazards=hazards)
        except Exception:
            logger.exception("防災知識檢索失敗")
            return None
        hits = [h for h in hits if h.distance <= config.KNOWLEDGE_MAX_DISTANCE]
        metrics.incr("knowledge.hit" if hits else "knowledge.no_match")
        return hits

    def knowledge_chunks(self, text: str | None) -> list[KnowledgeChunk]:
        """混合題 context 或純知識題回答裡的知識段落（依「■ 標題」找回）"""
        if self.knowledge is None or not text:
            return []
        by_title = {c.title: c for c in self.knowledge.chunks.values()}
        titles = [line[len(KNOWLEDGE_MARK):] for line in text.splitlines() if line.startswith(KNOWLEDGE_MARK)]
        return [by_title[t] for t in titles if t in by_title]

    def _shelter_context(self, user_message: str, lang: str = "zh"):
        """避難所這一側的路由（build_context 的 1～6）。固定句回中文，由 build_context 統一翻譯"""
        names = known_names_of(self._shelter_metadata() or [])
        plan = query_rules.analyze(user_message, known_names=names)
        if plan.out_of_scope:
            metrics.route("out_of_scope")
            return None, query_rules.out_of_scope_reply(plan.out_of_scope, lang)

        # 問的不是避難所（天氣、醫院、補助…）。不能只靠距離門檻：有範圍內地名的問句
        # 會產生 metadata 篩選，而門檻在那條路徑上依設計被跳過
        if plan.off_topic:
            metrics.route("off_topic")
            return None, query_rules.off_topic_reply(plan.off_topic, lang)

        # 錯一個字的避難所名稱（涙強國小）先改成真實名稱再檢索，才會走「點名」那條路、只送那一間；
        # 不改的話只剩「國小」篩選，模型要從一整串學校裡自己猜，實測會把容量答錯。
        # chat_stream 已經改過時這裡找不到錯字，不會重複處理
        corrected, _ = self.correct_typo(user_message)
        if corrected != user_message:
            user_message = corrected
            plan = query_rules.analyze(user_message, ignore_off_topic=True)

        if intent.is_explicit_simulation_query(user_message):
            metrics.route("simulation")
            return self._get_simulation_context()

        if intent.is_geo_query(user_message):
            coords = intent.extract_coords(user_message)
            if coords is not None:
                metrics.route("nearest")
                return self._get_nearest_context(*coords)
            if not self._is_simulation_followup(user_message) and intent.needs_coordinates(user_message):
                metrics.route("needs_coords")
                return None, NEEDS_COORDS_REPLY

        if self._is_simulation_followup(user_message):
            metrics.route("simulation")
            return self._get_simulation_context()

        if intent.asks_evacuation_advice(user_message):
            metrics.route("no_simulation_advice")
            return None, NO_SIMULATION_REPLY

        # 點名了資料裡沒有的場所（頭城國中、花蓮縣立游泳池）：由程式直接回答沒有這間，不交給模型。
        # 實測 llama3.2:3b 會替它編出容量與地址，或拿名稱相近的那間來答，連明寫的提示標頭都不照做
        unknown = self._unknown_shelter(user_message, lang)
        if unknown is not None:
            metrics.route("unknown_name")
            return None, unknown

        # 沒有地名、設施、容量條件，也沒有任何跟避難相關的線索（借錢、洗衣服、顧小孩）：
        # 這條路上距離門檻分不開離題與正當問題，交給模型的話它會列出一串避難所
        if not (plan.has_filter or plan.order_by_capacity) and self._lacks_shelter_clue(user_message):
            metrics.route("no_clue")
            return None, NO_RELEVANT_REPLY

        # 查詢向量要打 Ollama embedding，Ollama 掛掉時這裡會先炸；
        # 要回跟生成失敗一樣的降級訊息，而不是讓 /api/chat 變成 500
        metrics.route("rag")
        try:
            result = self.vector_store.search_result(user_message, plan=plan)
        except Exception:
            logger.exception("向量檢索失敗")
            return None, AI_UNAVAILABLE
        if result.text in (NO_DATA, NO_MATCH):
            return None, NO_RELEVANT_REPLY
        # 清單、排名、概況、點名：程式已經排好答案，不交給模型重抄（見 services/chat/direct_answer.py）
        direct = result.answer(lang)
        if direct:
            metrics.incr("chat.answer.direct")
            return None, direct
        return result.text, None

    def _shelter_metadata(self) -> list[dict] | None:
        try:
            return self.vector_store.shelter_metadata()
        except Exception:
            # 讀不到索引就不做名稱判斷，交給後面的檢索；檢索那一步自己有降級處理
            logger.exception("讀取避難所名單失敗")
            return None

    def correct_typo(self, user_message: str, lang: str = "zh") -> tuple[str, str | None]:
        """
        問句點名的避難所錯一個字、而且只像某一間時，回傳 (改成真實名稱的問句, 給使用者看的說明)；
        沒有錯字回 (原問句, None)。說明由程式加在回覆開頭，不交給模型決定要不要提
        """
        shelters = self._shelter_metadata()
        if not shelters:
            return user_message, None
        forms = {form: display_name(m["name"]) for m in shelters for form in (display_name(m["name"]), *aliases_of(m["name"]))}
        found = query_rules.typo_correction(user_message, list(forms))
        if found is None:
            return user_message, None
        written, form = found
        real = forms[form]
        return user_message.replace(written, real), typo_note(written, real, lang)

    def _lacks_shelter_clue(self, user_message: str) -> bool:
        names = known_names_of(self._shelter_metadata() or [])
        # 名單是空的（索引還沒建好、讀不到）就不判斷，照原本的方式檢索
        return bool(names) and not query_rules.has_shelter_clue(user_message, names)

    def _unknown_shelter(self, user_message: str, lang: str = "zh") -> str | None:
        """問句點名的場所不在索引裡時回傳固定回覆；在的話（或判斷不了）回 None，照常檢索"""
        shelters = self._shelter_metadata()
        if shelters is None:
            return None
        name = query_rules.unknown_shelter_name(user_message, known_names_of(shelters))
        if name is None:
            return None
        return unknown_shelter_reply(name, shelters, lang)

    def build_prompt(self, user_message: str, shelter_context: str, with_simulation: bool = False, lang: str = "zh") -> str:
        """
        模擬摘要只附在走模擬路線的問題上。一般檢索、最近避難所也附的話，小模型會把兩份資料混在一起：
        實測南山附近淹水模擬進行中問「我們一家五口可以去哪」，回答把模擬裡的南山國小
        配上檢索結果裡另一間學校的鄉鎮與容量。收容人數本來就已經回寫進資料庫與向量文件，
        一般檢索看到的負載跟模擬一致，不需要再靠摘要補。

        混合題只把避難所那一段和問避難所的那半句交給模型：知識原文由程式另外送，
        整句都給的話模型會順手回答「颱風要準備什麼」，又回到自己改寫的老問題
        """
        if KNOWLEDGE_HEADER in shelter_context:
            shelter_context = shelter_part(shelter_context)
            user_message = query_rules.shelter_clauses(user_message)
        full_context = f"{SHELTER_HEADER}\n{shelter_context}"
        summary = self._simulation_summary() if with_simulation else ""
        if summary:
            full_context += f"\n\n【目前災害模擬結果】\n{summary}"

        if lang == "en":
            return f"""{full_context}

【使用者問題】
{user_message}

Instructions:
1. Answer directly from the data above, starting with the shelter name exactly as written in Chinese.
   For one shelter, give its address, capacity, current occupancy and free spaces.
2. For several shelters, one per line: "Name: township, capacity N, M spaces left". List every matching shelter.
3. Copy the numbers exactly; do not add them up or change them.
4. Write the answer in English, but keep every shelter name and address in Chinese."""

        # 列舉題（一個縣 20 筆）要能在 num_predict 內列完，所以規定每間一行、只講關鍵欄位
        return f"""{full_context}

【使用者問題】
{user_message}

回答要求：
1. 直接用上面的資料回答，回答時先寫出避難所名稱。問某一間避難所時只回答那一間，引用該筆的地址、容量、目前收容人數與剩餘空位。
2. 列舉多間避難所時每間一行，格式「名稱：鄉鎮，容量 N 人，剩餘空位 M 人」，把資料裡符合的全部列完，不要省略。
3. 資料裡的數字原樣引用，不要自行加總或改寫。
4. 只能用繁體中文，不得出現英文。"""

    def chat_stream(self, user_message: str, lang: str = "zh"):
        """
        串流回答：逐段 yield 事件 dict，type 有 delta（文字片段）與 error（固定錯誤句）。
        跟 chat() 一樣不對外拋例外，所有錯誤都轉成使用者看得懂的訊息，
        呼叫端只負責把 text 接起來或往下送。lang="en" 時回答用英文（問題仍是中文最準）
        """
        started = time.monotonic()
        # 錯字在這裡就改掉：prompt 裡的問題也要是真實名稱，模型才不會照抄錯字回答
        user_message, typo = self.correct_typo(user_message, lang)
        try:
            shelter_context, early_reply = self.build_context(user_message, lang)
        except Exception:
            # 任何查詢層的例外都不該變成 500；細節只進 log，不回給使用者
            metrics.route("context_error")
            logger.exception("建立查詢內容失敗")
            self._log_summary("early_reply", "context_error", 0, len(GENERIC_ERROR), started)
            yield {"type": "error", "text": tr(GENERIC_ERROR, lang)}
            return
        if early_reply:
            # 檢索層就答得出來（範圍外、要座標、沒有相關資料），不必打 LLM，一次給完
            metrics.incr("chat.answer.early_reply")
            self._log_summary("early_reply", "ok", 0, len(early_reply), started)
            yield {"type": "delta", "text": f"{typo}\n{early_reply}" if typo else early_reply}
            return

        metrics.incr("chat.answer.generated")
        if typo:
            yield {"type": "delta", "text": typo + "\n"}
        # 混合題：知識原文由程式先送出，模型只負責後面的避難所清單；出處附在最後
        cited = self.knowledge_chunks(shelter_context)
        if cited:
            yield {"type": "delta", "text": knowledge_text(cited, lang) + "\n\n"}
        prompt = self.build_prompt(
            user_message, shelter_context, with_simulation=metrics.last_route() == "simulation", lang=lang,
        )
        reply_chars = 0
        guard = LineGuard(self.prompt_names(prompt))
        try:
            for chunk in self.generate_stream(prompt, lang):
                reply_chars += len(chunk)
                text = guard.feed(chunk)
                if text:
                    yield {"type": "delta", "text": text}
            tail = guard.finish()
            if tail:
                yield {"type": "delta", "text": tail}
            if guard.dropped:
                metrics.incr("chat.guard.dropped_lines", len(guard.dropped))
                logger.info("拿掉 %d 行提到資料以外避難所的回答：%s", len(guard.dropped), guard.dropped)
        except Exception:
            # 斷線前已經生成、還卡在防護緩衝裡的那一行照樣檢查後送出，使用者才看得到斷在哪裡
            tail = guard.finish()
            if tail:
                yield {"type": "delta", "text": tail}
            # 已經吐出文字才斷掉是「中斷」，一個字都沒吐出來是「服務不可用」，兩者要分開看
            outcome = "interrupted" if reply_chars else "unavailable"
            metrics.incr("chat.generation." + outcome)
            logger.exception("Ollama 串流生成失敗")
            self._log_summary("generated", outcome, len(shelter_context), reply_chars, started)
            yield {"type": "error", "text": tr(STREAM_INTERRUPTED if reply_chars else AI_UNAVAILABLE, lang)}
            return
        if not reply_chars:
            # 連線沒問題但模型一個字都沒給：不要讓前端收到空白泡泡
            metrics.incr("chat.generation.empty")
            logger.warning("Ollama 串流沒有產生任何文字")
            self._log_summary("generated", "empty", len(shelter_context), 0, started)
            yield {"type": "error", "text": tr(AI_UNAVAILABLE, lang)}
            return
        # 被排除的避難所由程式補在最後，不交給模型：實測 3 次回答都照「名稱：容量、剩餘空位」的格式
        # 把澳花國小列成剩餘 150 人，附註整個被省略——看起來就像豪雨時可以去。靠近區域的警告同理
        if metrics.last_route() == "simulation":
            notes = hazard_lines((self._snapshot() or {}).get("evacuation") or {}, lang)
            if notes:
                yield {"type": "delta", "text": "\n\n" + "\n".join(notes)}
        if cited:
            yield {"type": "delta", "text": "\n\n" + sources_text(cited, lang)}
        metrics.incr("chat.generation.ok")
        self._log_summary("generated", "ok", len(shelter_context), reply_chars, started)

    def prompt_names(self, prompt: str) -> list[str]:
        """prompt 裡出現的避難所名稱（顯示名稱或別名）：回答只能講這些"""
        return supported_names(prompt, known_names_of(self._shelter_metadata() or []))

    def guard_reply(self, reply: str, prompt: str) -> tuple[str, list[str]]:
        """非串流版的回答防護（評測用），跟 chat_stream 的 LineGuard 是同一套判斷"""
        return filter_lines(reply, self.prompt_names(prompt))

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

    def chat(self, user_message: str, lang: str = "zh") -> str:
        """非串流版：把 chat_stream() 的片段接起來，給不吃 SSE 的呼叫端（腳本、測試）用"""
        return "".join(event["text"] for event in self.chat_stream(user_message, lang)).strip()

    def generate(self, prompt: str, lang: str = "zh") -> dict:
        """一次拿完整的生成結果（評測用），見 services/llm_client.generate"""
        return llm_client.generate(prompt, lang)

    def generate_stream(self, prompt: str, lang: str = "zh"):
        """串流生成，見 services/llm_client.generate_stream"""
        return llm_client.generate_stream(prompt, lang)
