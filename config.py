import os

# 環境變數解析失敗時退回預設值，但要記下來，validate() 才能提醒使用者設定被忽略了
PARSE_WARNINGS: list[str] = []


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        PARSE_WARNINGS.append(f"{key}={raw!r} 不是數字，改用預設值 {default}")
        return default


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        PARSE_WARNINGS.append(f"{key}={raw!r} 不是整數，改用預設值 {default}")
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    PARSE_WARNINGS.append(f"{key}={raw!r} 不是布林值（true/false），改用預設值 {default}")
    return default


POSTGRES_DB = os.environ.get("POSTGRES_DB", "disaster_db")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "disaster")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "disaster_db")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
DB_POOL_MIN = _env_int("DB_POOL_MIN", 1)
# asyncio.to_thread 預設最多 cpu+4 條執行緒同時查資料庫，池子要跟得上
DB_POOL_MAX = _env_int("DB_POOL_MAX", 10)
# 資料庫卡住時請求不能無限等：連線幾秒內連不上就放棄，單一 SQL 跑超過上限由伺服器端中止
DB_CONNECT_TIMEOUT = _env_int("DB_CONNECT_TIMEOUT", 5)
DB_STATEMENT_TIMEOUT_MS = _env_int("DB_STATEMENT_TIMEOUT_MS", 15000)

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_TEMPERATURE = _env_float("OLLAMA_TEMPERATURE", 0.3)
# 一次列舉題可能塞 20 至 30 筆文件（約 3000 字），加系統提示會超過 Ollama 的預設上下文，
# 沒明確設 num_ctx 前段資料會被靜默截掉；輸出也要夠長才列得完
OLLAMA_NUM_CTX = _env_int("OLLAMA_NUM_CTX", 8192)
OLLAMA_NUM_PREDICT = _env_int("OLLAMA_NUM_PREDICT", 800)
OLLAMA_TIMEOUT = _env_int("OLLAMA_TIMEOUT", 120)

EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "ollama")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "bge-m3")
EMBEDDING_TIMEOUT = _env_int("EMBEDDING_TIMEOUT", 120)

RAG_TOP_K = _env_int("RAG_TOP_K", 10)
# 純語意檢索的 cosine 距離上限：最接近的一筆超過這個值就當沒有相關資料。
# 依 bge-m3 的評測：無關問題（天氣、補助）最近也在 0.59 左右，有相關資料的問題最遠約 0.48
RAG_MAX_DISTANCE = _env_float("RAG_MAX_DISTANCE", 0.56)
# 同時進行的 LLM 生成上限。每次生成最長 OLLAMA_TIMEOUT 秒，佔用一條工作執行緒；
# 不設上限的話幾個人同時發問就會把執行緒池吃光，地圖載入與 readiness 一起卡住
CHAT_MAX_CONCURRENT = _env_int("CHAT_MAX_CONCURRENT", 2)
# /api/chat 沒有登入保護，而每一題都要吃一次 LLM 生成。併發名額只擋「同時幾個」，
# 擋不住同一個人連續問不停，所以再加一層單一來源的用量上限。設 0 可停用。
# 一題生成本來就要 10 至 60 秒、前端送出後也會鎖住輸入，正常使用碰不到 15 次／分鐘
CHAT_RATE_LIMIT = _env_int("CHAT_RATE_LIMIT", 15)
CHAT_RATE_WINDOW = _env_int("CHAT_RATE_WINDOW", 60)
# 只有在前面確定有反向代理時才信任 X-Forwarded-For / X-Forwarded-Proto；
# 直接對外時這兩個 header 任何人都能自己帶，用來繞過登入節流
TRUST_PROXY_HEADERS = _env_bool("TRUST_PROXY_HEADERS", False)
SYNC_API_KEY = os.environ.get("SYNC_API_KEY", "")
# 寫入端點（模擬 / 收容人數回寫 / 重置）的兩種通行方式：
# 1. 登入後的 session cookie（操作員用，瀏覽器自動帶）
# 2. X-API-Key 標頭（curl / 排程腳本用，選配）
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "").strip()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "").strip()
SESSION_HOURS = _env_float("SESSION_HOURS", 12)
WRITE_API_KEY = os.environ.get("WRITE_API_KEY", "")

# ChromaDB 落地路徑；留空代表用記憶體索引（測試與本機直跑的預設）
CHROMA_PATH = os.environ.get("CHROMA_PATH", "").strip()


# 少了這些變數服務仍起得來，但每一條查詢都會壞，所以啟動時就要擋下來
REQUIRED_SETTINGS = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_HOST", "POSTGRES_PORT")

# .env.example 裡的佔位字串，沿用等於沒設定
PLACEHOLDERS = {"change_me", "change_me_to_a_long_random_string", "change_me_to_another_long_random_string"}


class ConfigError(RuntimeError):
    pass


def _bounds_errors() -> list[str]:
    """
    數值設定的下限。這些值填錯服務照樣起得來，但行為會壞掉：
    SESSION_HOURS=0 所有登入立刻過期、DB_POOL_MAX < DB_POOL_MIN 連線池建不起來、RAG_TOP_K=0 檢索永遠是空的。
    """
    checks = [
        ("SESSION_HOURS", SESSION_HOURS, SESSION_HOURS > 0, "必須大於 0"),
        ("DB_POOL_MIN", DB_POOL_MIN, DB_POOL_MIN >= 1, "至少要 1"),
        ("DB_POOL_MAX", DB_POOL_MAX, DB_POOL_MAX >= DB_POOL_MIN, f"不能小於 DB_POOL_MIN（{DB_POOL_MIN}）"),
        ("DB_CONNECT_TIMEOUT", DB_CONNECT_TIMEOUT, DB_CONNECT_TIMEOUT >= 1, "至少要 1 秒"),
        ("DB_STATEMENT_TIMEOUT_MS", DB_STATEMENT_TIMEOUT_MS, DB_STATEMENT_TIMEOUT_MS >= 1000, "至少要 1000 毫秒"),
        ("RAG_TOP_K", RAG_TOP_K, RAG_TOP_K >= 1, "至少要 1"),
        ("RAG_MAX_DISTANCE", RAG_MAX_DISTANCE, 0 < RAG_MAX_DISTANCE <= 2, "必須介於 0 到 2（cosine 距離）"),
        ("CHAT_MAX_CONCURRENT", CHAT_MAX_CONCURRENT, CHAT_MAX_CONCURRENT >= 1, "至少要 1"),
        ("CHAT_RATE_LIMIT", CHAT_RATE_LIMIT, CHAT_RATE_LIMIT >= 0, "不能是負數（0 代表停用）"),
        ("CHAT_RATE_WINDOW", CHAT_RATE_WINDOW, CHAT_RATE_WINDOW >= 1, "至少要 1 秒"),
        ("OLLAMA_TIMEOUT", OLLAMA_TIMEOUT, OLLAMA_TIMEOUT >= 1, "至少要 1 秒"),
        ("EMBEDDING_TIMEOUT", EMBEDDING_TIMEOUT, EMBEDDING_TIMEOUT >= 1, "至少要 1 秒"),
        ("OLLAMA_NUM_CTX", OLLAMA_NUM_CTX, OLLAMA_NUM_CTX >= 1024, "至少要 1024，否則系統提示加資料就放不下"),
        ("OLLAMA_NUM_PREDICT", OLLAMA_NUM_PREDICT, OLLAMA_NUM_PREDICT >= 1, "至少要 1"),
        ("OLLAMA_TEMPERATURE", OLLAMA_TEMPERATURE, 0 <= OLLAMA_TEMPERATURE <= 2, "必須介於 0 到 2"),
    ]
    return [f"{key}={value!r} {reason}" for key, value, ok, reason in checks if not ok]


def validate() -> list[str]:
    """
    檢查啟動必要設定。缺少必填變數或數值超出合理範圍直接丟 ConfigError；
    其餘只是提醒，用 list 回傳給呼叫端記 log。
    """
    missing = [key for key in REQUIRED_SETTINGS if not str(globals()[key]).strip()]
    if missing:
        raise ConfigError(
            "缺少必要環境變數：" + "、".join(missing)
            + "。請執行 cp .env.example .env 並填入實際值後重新啟動。"
        )
    bad = _bounds_errors()
    if bad:
        raise ConfigError("環境變數超出合理範圍：" + "；".join(bad))

    warnings = list(PARSE_WARNINGS)
    if POSTGRES_PASSWORD in PLACEHOLDERS:
        warnings.append("POSTGRES_PASSWORD 仍是 .env.example 的預設值，請改成自訂密碼")
    if not SYNC_API_KEY:
        warnings.append("未設定 SYNC_API_KEY，/api/sync 手動同步已停用")
    elif SYNC_API_KEY in PLACEHOLDERS:
        warnings.append("SYNC_API_KEY 仍是 .env.example 的預設值，請改成隨機長字串")
    if not (ADMIN_USERNAME and ADMIN_PASSWORD):
        warnings.append("未設定 ADMIN_USERNAME / ADMIN_PASSWORD，無法登入；模擬、收容人數回寫與重置端點只剩 X-API-Key 可用")
    elif ADMIN_PASSWORD in PLACEHOLDERS:
        warnings.append("ADMIN_PASSWORD 仍是 .env.example 的預設值，請改成自訂密碼")
    if not SESSION_SECRET:
        warnings.append("未設定 SESSION_SECRET，啟動時會隨機產生，服務重啟後所有登入都要重來")
    elif SESSION_SECRET in PLACEHOLDERS:
        warnings.append("SESSION_SECRET 仍是 .env.example 的預設值，請改成隨機長字串")
    if WRITE_API_KEY in PLACEHOLDERS:
        warnings.append("WRITE_API_KEY 仍是 .env.example 的預設值，請改成隨機長字串或留空停用")
    if CHAT_RATE_LIMIT <= 0:
        warnings.append("CHAT_RATE_LIMIT 設為 0，/api/chat 沒有來源用量上限，任何人都能無限呼叫 LLM")
    if not CHROMA_PATH:
        warnings.append("未設定 CHROMA_PATH，向量索引只存在記憶體，每次重啟都要重新 embedding")
    return warnings
