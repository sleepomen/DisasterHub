"""
各端點共用的物件與函式。整個程式只有一份：資料庫、向量索引、知識索引、聊天服務、人口模型、
登入 session 與節流、聊天的限流與生成名額。路由一律用 deps.xxx 取用，測試 patch 這裡就會生效
"""
import functools
import json
import logging
import secrets
import threading

from fastapi import HTTPException, Request

import config
from repositories.shelter_repository import ShelterRepository
from services.chat.service import ChatService
from services.ingest.sync import HAZARD_FILES, DataSyncService
from services.retrieval.knowledge import KnowledgeStore
from services.retrieval.vector_store import VectorStore
from services.simulation.population import PopulationModel
from services.web import auth
from services.web.map_service import MapService
from services.web.metrics import metrics
from services.web.rate_limit import RateLimiter

logger = logging.getLogger(__name__)

repo = ShelterRepository()
map_service = MapService()
vector_store = VectorStore()
# 防災知識文件跟避難所索引共用同一個 ChromaDB client 與 embedding 模型，各自一個 collection
knowledge_store = KnowledgeStore(embedding_function=vector_store.ef, client=vector_store.client)
chat_service = ChatService(vector_store=vector_store, repo=repo, knowledge=knowledge_store)
population_model = PopulationModel()
session_manager = auth.SessionManager()
login_throttle = auth.LoginThrottle()
# LLM 生成的併發名額：一次生成最長 OLLAMA_TIMEOUT 秒，超出名額的問題立刻回 429，
# 不排隊、不佔執行緒，地圖與 readiness 才不會跟著 Ollama 一起卡住
chat_slots = threading.BoundedSemaphore(config.CHAT_MAX_CONCURRENT)
# 單一來源的用量上限。/api/chat 不需要登入（災時任何人都要問得到），
# 但也因此沒有任何東西擋住有人拿它當免費 LLM 用，所以按來源限流
chat_limiter = RateLimiter(config.CHAT_RATE_LIMIT, config.CHAT_RATE_WINDOW)
# 被擋掉的請求也要留下數字：rate_limit 偏高代表有人在刷，busy 偏高代表生成名額不夠用，
# 兩者的處理方式完全不同，混在一個 429 裡看不出來
metrics.register("chat.rejected.rate_limit", "chat.rejected.busy")


# ── 資料同步與收容人數 ─────────────────────────────────

def sync_and_reindex(force: bool = False) -> int:
    repo.ensure_schema()
    DataSyncService().sync()
    shelters = repo.get_all_shelters()
    vector_store.build_index(shelters, force=force)
    build_knowledge()
    return len(shelters)


def build_knowledge() -> None:
    """
    知識文件跟資料庫無關，壞了（格式錯、embedding 失敗）也不該讓避難所同步一起失敗：
    記下錯誤就好，知識題會回「目前無法使用」，下次同步再試
    """
    try:
        knowledge_store.build()
    except Exception:
        logger.exception("防災知識索引建立失敗，知識題暫時無法回答")


@functools.cache
def hazard_zones_geojson() -> dict:
    path = HAZARD_FILES["debris_flow"]
    if not path.exists():
        return {"type": "FeatureCollection", "features": []}
    return json.loads(path.read_text(encoding="utf-8"))


def apply_occupancy(occupancy: dict[str, int]) -> dict:
    """
    把收容人數寫進資料庫，只對真的有變動的避難所重算向量，
    再同步聊天服務裡的模擬快照。回傳更新統計與最新佔用表給前端對齊畫面。
    """
    before = {s.name: s.current_people for s in repo.get_all_shelters()}
    updated = repo.set_occupancy(occupancy)
    after = repo.get_all_shelters()
    changed = [s for s in after if before.get(s.name) != s.current_people]
    reindexed = vector_store.upsert_shelters(changed)
    chat_service.refresh_occupancy(after)
    return {
        "updated": updated,
        "reindexed": reindexed,
        "occupancy": {s.name: s.current_people for s in after},
    }


def alert_summary() -> list[dict]:
    """目前生效的土石流警戒，給地圖疊圖與模擬結果用；查不到就當成沒有，不影響其他功能"""
    try:
        alerts = repo.active_alerts()
    except Exception:
        logger.exception("查詢土石流警戒失敗")
        return []
    return [
        {
            "ref_no": a["ref_no"], "level": a["level"],
            "place": f"{a['county'] or ''}{a['town'] or ''}{a['village'] or ''}",
            "issued_at": a["issued_at"].isoformat() if a["issued_at"] else None,
            "fetched_at": a["fetched_at"].isoformat(),
            "road_km": round(a["road_m"] / 1000, 2),
        }
        for a in alerts
    ]


# ── 權限 ──────────────────────────────────────────────

def current_user(http_request: Request) -> str | None:
    return session_manager.verify(http_request.cookies.get(auth.COOKIE_NAME))


def require_sync_access(http_request: Request, x_api_key: str) -> None:
    """
    /api/sync 的三種通行方式：SYNC_API_KEY、登入的 session、或寫入金鑰 WRITE_API_KEY。
    只認 SYNC_API_KEY 的話，沒設這個變數的部署在啟動同步失敗後就只剩重啟容器一條路
    （索引空的時候 readiness 一直是 503），操作員要能自己把資料同步回來。
    """
    for expected in (config.SYNC_API_KEY, config.WRITE_API_KEY):
        if expected and x_api_key and secrets.compare_digest(x_api_key, expected):
            return
    if current_user(http_request):
        return
    if not (config.SYNC_API_KEY or config.WRITE_API_KEY or auth.credentials_configured()):
        raise HTTPException(
            status_code=503,
            detail="伺服器未設定 SYNC_API_KEY / WRITE_API_KEY / ADMIN_USERNAME，手動同步已停用",
        )
    raise HTTPException(status_code=401, detail="請先登入或提供有效的 API key")


def require_write_access(http_request: Request, x_api_key: str) -> None:
    """
    模擬 / 收容人數回寫 / 重置：登入的 session cookie 或 X-API-Key 擇一。
    兩種都沒設定時停用（503），而不是放行。
    """
    if not auth.credentials_configured() and not config.WRITE_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="伺服器未設定 ADMIN_USERNAME / ADMIN_PASSWORD，模擬與收容人數寫入已停用",
        )
    if current_user(http_request):
        return
    if x_api_key and config.WRITE_API_KEY and secrets.compare_digest(x_api_key, config.WRITE_API_KEY):
        return
    raise HTTPException(status_code=401, detail="請先登入")


def client_key(http_request: Request) -> str:
    """
    登入節流用的來源識別。反向代理後面看到的是代理的位址，要改用它轉送的第一個 IP；
    但這個 header 誰都能自己帶，直接對外時信任它等於讓人隨便換 key 繞過節流，
    所以只有 TRUST_PROXY_HEADERS 開啟時才讀。
    """
    if config.TRUST_PROXY_HEADERS:
        forwarded = http_request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return http_request.client.host if http_request.client else "unknown"


def request_is_https(http_request: Request) -> bool:
    if http_request.url.scheme == "https":
        return True
    return config.TRUST_PROXY_HEADERS and http_request.headers.get("x-forwarded-proto", "").lower() == "https"
