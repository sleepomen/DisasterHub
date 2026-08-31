# 以下是新增的
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

RAG_TOP_K = _env_int("RAG_TOP_K", 10)
SYNC_API_KEY = os.environ.get("SYNC_API_KEY", "")
