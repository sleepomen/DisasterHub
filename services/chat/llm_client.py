"""
呼叫 Ollama 生成回答：系統提示、請求內容、一次拿完與串流兩種生成。

失敗一律拋例外，由 ChatService 轉成使用者看得懂的訊息
"""
import json
import logging
import time

import requests

import config

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

# 英文模式還會打模型的路徑（座標、模擬、純語意題）。資料仍是中文：避難所名稱與地址照抄中文，
# 不可以自己翻或轉拼音（資料沒有英文名稱，翻出來的名字在現場對不上招牌，回答防護也認不得）
SYSTEM_PROMPT_EN = """You are the decision assistant of a disaster shelter management system for eastern Taiwan (Yilan, Hualien, Taitung).
Rules:
1. Answer in English. Shelter names and addresses in the data are in Chinese:
   copy them exactly as written, never translate or romanize them.
2. Use only the data in 【避難所資料】. Do not guess or invent. Copy names and numbers exactly and do not recalculate them.
3. The shelter data has already been filtered for this question. If it lists shelters, answer with them; never say there is no data.
4. Only when the data says there is no data, or the question has nothing to do with shelters, answer "No relevant information."
5. Be brief and practical: shelter name, location, capacity and free spaces.
6. No introductions, no small talk, no follow-up questions."""


class GenerationTimeout(RuntimeError):
    """整段生成超過 OLLAMA_TIMEOUT，串流已中止"""


def payload(prompt: str, stream: bool, lang: str = "zh") -> dict:
    """生成請求的內容。串流與非串流共用同一份，評測量到的才跟線上同一個模型設定"""
    return {
        "model": config.OLLAMA_MODEL,
        "system": SYSTEM_PROMPT_EN if lang == "en" else SYSTEM_PROMPT,
        "prompt": prompt,
        "stream": stream,
        "options": {
            "temperature": config.OLLAMA_TEMPERATURE,
            "num_predict": config.OLLAMA_NUM_PREDICT,
            "num_ctx": config.OLLAMA_NUM_CTX,
        },
    }


def generate(prompt: str, lang: str = "zh") -> dict:
    """
    打 Ollama 生成，一次拿完整的 JSON（response、done_reason、eval_count …）。
    生成端評測要靠它拿 token 數與截斷原因；失敗直接拋例外由呼叫端處理。
    """
    response = requests.post(
        f"{config.OLLAMA_HOST}/api/generate",
        json=payload(prompt, False, lang),
        timeout=config.OLLAMA_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def generate_stream(prompt: str, lang: str = "zh"):
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
        json=payload(prompt, True, lang),
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
