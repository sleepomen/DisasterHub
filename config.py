import os


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except ValueError:
        return default


POSTGRES_DB = os.environ.get("POSTGRES_DB", "disaster_db")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "disaster")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "")
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "disaster_db")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
DB_POOL_MIN = _env_int("DB_POOL_MIN", 1)
DB_POOL_MAX = _env_int("DB_POOL_MAX", 5)

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_TEMPERATURE = _env_float("OLLAMA_TEMPERATURE", 0.3)
OLLAMA_NUM_PREDICT = _env_int("OLLAMA_NUM_PREDICT", 300)
OLLAMA_TIMEOUT = _env_int("OLLAMA_TIMEOUT", 120)

EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "ollama")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "bge-m3")
EMBEDDING_TIMEOUT = _env_int("EMBEDDING_TIMEOUT", 120)

RAG_TOP_K = _env_int("RAG_TOP_K", 10)
SYNC_API_KEY = os.environ.get("SYNC_API_KEY", "")

# ChromaDB 落地路徑；留空代表用記憶體索引（測試與本機直跑的預設）
CHROMA_PATH = os.environ.get("CHROMA_PATH", "").strip()


# 少了這些變數服務仍起得來，但每一條查詢都會壞，所以啟動時就要擋下來
REQUIRED_SETTINGS = ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_HOST", "POSTGRES_PORT")

# .env.example 裡的佔位字串，沿用等於沒設定
PLACEHOLDERS = {"change_me", "change_me_to_a_long_random_string"}


class ConfigError(RuntimeError):
    pass


def validate() -> list[str]:
    """
    檢查啟動必要設定。缺少必填變數直接丟 ConfigError；
    其餘只是提醒，用 list 回傳給呼叫端記 log。
    """
    missing = [key for key in REQUIRED_SETTINGS if not str(globals()[key]).strip()]
    if missing:
        raise ConfigError(
            "缺少必要環境變數：" + "、".join(missing)
            + "。請執行 cp .env.example .env 並填入實際值後重新啟動。"
        )

    warnings = []
    if POSTGRES_PASSWORD in PLACEHOLDERS:
        warnings.append("POSTGRES_PASSWORD 仍是 .env.example 的預設值，請改成自訂密碼")
    if not SYNC_API_KEY:
        warnings.append("未設定 SYNC_API_KEY，/api/sync 手動同步已停用")
    elif SYNC_API_KEY in PLACEHOLDERS:
        warnings.append("SYNC_API_KEY 仍是 .env.example 的預設值，請改成隨機長字串")
    if not CHROMA_PATH:
        warnings.append("未設定 CHROMA_PATH，向量索引只存在記憶體，每次重啟都要重新 embedding")
    return warnings
