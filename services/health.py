import logging

import requests

import config

logger = logging.getLogger(__name__)

# readiness 要能很快回答，不能卡在依賴的 timeout 上
OLLAMA_PING_TIMEOUT = 3


def check_database(repo) -> tuple[bool, str]:
    try:
        repo.ping()
        return True, "ok"
    except Exception as e:
        logger.warning("readiness：資料庫檢查失敗：%s", e)
        return False, f"unreachable ({type(e).__name__})"


def check_index(vector_store) -> tuple[bool, str]:
    try:
        count = vector_store.count()
    except Exception as e:
        logger.warning("readiness：向量索引檢查失敗：%s", e)
        return False, f"unavailable ({type(e).__name__})"
    if count == 0:
        return False, "empty"
    return True, f"{count} documents"


def check_ollama() -> tuple[bool, str]:
    try:
        response = requests.get(f"{config.OLLAMA_HOST}/api/tags", timeout=OLLAMA_PING_TIMEOUT)
        response.raise_for_status()
        return True, "ok"
    except Exception as e:
        logger.warning("readiness：Ollama 檢查失敗：%s", e)
        return False, f"unreachable ({type(e).__name__})"
