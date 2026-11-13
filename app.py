"""
FastAPI 應用程式：日誌、啟動同步、中介層、健康檢查、首頁與靜態檔。
各功能的 API 在 api/（見 api/__init__.py），這裡只把它們掛上去
"""
import asyncio
import contextvars
import logging
import re
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders

import config

# 每一行 log 都帶 request id：一個請求在 docker logs 裡的幾行才能串起來，
# 使用者回報「剛才那題壞了」時也能直接用回應標頭上的 id 去撈
REQUEST_ID_HEADER = "X-Request-ID"
# 這個值會進 log，不檢查格式等於讓人把任意內容（含換行）塞進日誌
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """掛在 handler 上而不是 logger 上：這樣所有子 logger 傳上來的紀錄都會被補上欄位"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


# 日誌與設定檢查要在建立資料庫、索引等物件（api.deps）之前，它們初始化時的訊息才有同樣的格式
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s [%(request_id)s]: %(message)s",
)
for _handler in logging.getLogger().handlers:
    _handler.addFilter(RequestIdFilter())
logger = logging.getLogger(__name__)

try:
    for warning in config.validate():
        logger.warning("設定提醒：%s", warning)
except config.ConfigError as e:
    logger.error("設定檢查失敗：%s", e)
    raise

from api import admin, chat, deps, map_data, session, simulation  # noqa: E402

# 測試與操作時常用的物件，從 app 也拿得到（實際都在 api.deps，同一個物件）
from api.deps import (  # noqa: E402, F401
    chat_limiter,
    chat_service,
    chat_slots,
    client_key,
    login_throttle,
    session_manager,
)
from services.geo.hazard_alerts import poll_alerts  # noqa: E402
from services.web import health  # noqa: E402
from services.web.metrics import metrics  # noqa: E402, F401

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
INDEX_FILE = STATIC_DIR / "index.html"

# 啟動同步失敗後的重試間隔（秒）。資料庫還沒起來、Ollama 還在載 embedding 模型都是
# 幾十秒就會好的暫時狀況，但索引空著 readiness 就一直回 503，容器也一直 unhealthy。
# 自己退避重試，不要求人剛好在線上去呼叫 /api/sync
STARTUP_RETRY_DELAYS = (5, 15, 30, 60, 120, 300)


async def retry_startup_sync() -> None:
    for delay in STARTUP_RETRY_DELAYS:
        await asyncio.sleep(delay)
        try:
            count = await asyncio.to_thread(deps.sync_and_reindex)
        except Exception as e:
            logger.warning("啟動同步重試失敗（間隔 %d 秒的那次）：%s", delay, e)
            continue
        logger.info("啟動同步重試成功，共載入 %d 筆避難所", count)
        return
    logger.error(
        "啟動同步重試全部失敗，索引仍是空的，readiness 會持續回 503。"
        "請修好資料庫或 Ollama 之後呼叫 /api/sync，或重啟服務"
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("啟動時執行資料同步與向量索引建立...")
    retry_task = None
    try:
        count = await asyncio.to_thread(deps.sync_and_reindex)
        logger.info("啟動完成，共載入 %d 筆避難所", count)
    except Exception:
        logger.exception("啟動同步失敗，服務仍會啟動，接下來會在背景自動重試")
        retry_task = asyncio.create_task(retry_startup_sync())
    # 即時土石流警戒：路線封閉或繞開警戒範圍內的道路
    alert_task = asyncio.create_task(poll_alerts(deps.repo)) if config.ALERT_POLL_SECONDS > 0 else None
    yield
    for task in (retry_task, alert_task):
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


class RequestIdMiddleware:
    """
    純 ASGI 中介層（不是 BaseHTTPMiddleware）：整個請求——包含 SSE 的串流回應主體——
    都在 await self.app(...) 裡面跑完，所以 contextvar 對串流那段也有效，
    而且不會多一層 task 與佇列去影響逐段送出的時序。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = self._incoming_id(scope) or uuid.uuid4().hex[:8]

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            await send(message)

        token = request_id_var.set(request_id)
        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)

    @staticmethod
    def _incoming_id(scope) -> str:
        """只有確定前面有代理時才沿用上游的 id，而且要通過格式檢查"""
        if not config.TRUST_PROXY_HEADERS:
            return ""
        wanted = REQUEST_ID_HEADER.lower().encode()
        for key, value in scope.get("headers", []):
            if key.lower() == wanted:
                candidate = value.decode("latin-1", "replace")
                return candidate if SAFE_REQUEST_ID.match(candidate) else ""
        return ""


class RevalidateStaticMiddleware:
    """
    首頁與 /static/ 底下的檔案加上 Cache-Control: no-cache：瀏覽器照樣快取，但每次都先用
    ETag 問伺服器有沒有更新，沒變就回 304、沿用快取。沒有這個標頭時瀏覽器會自己猜能用多久，
    2026-11-09 改版後重新整理仍看到舊的 style.css，就是這個原因
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if path != "/" and not path.startswith("/static/"):
            await self.app(scope, receive, send)
            return

        async def send_with_cache_control(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if "cache-control" not in headers:
                    headers.append("Cache-Control", "no-cache")
            await send(message)

        await self.app(scope, receive, send_with_cache_control)


app = FastAPI(lifespan=lifespan)
app.add_middleware(RevalidateStaticMiddleware)
app.add_middle/ware(RequestIdMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
for module in (session, map_data, simulation, chat, admin):
    app.include_router(module.router)


# liveness：只表示 process 還活著
@app.get("/health")
async def health_check():
    return {"status": "ok"}


# readiness：真的去戳依賴。
# 資料庫與向量索引重啟後可以自行恢復，列為必要條件；
# Ollama 跑在宿主機，重啟容器救不了它，只回報狀態不影響判定。
@app.get("/health/ready")
async def readiness_check(response: Response):
    db_ok, db_detail = await asyncio.to_thread(health.check_database, deps.repo)
    index_ok, index_detail = await asyncio.to_thread(health.check_index, deps.vector_store)
    ollama_ok, ollama_detail = await asyncio.to_thread(health.check_ollama)
    knowledge_ok, knowledge_detail = deps.knowledge_store.status()

    ready = db_ok and index_ok
    if not ready:
        response.status_code = 503

    return {
        "status": "ready" if ready else "not_ready",
        "checks": {
            "database": {"ok": db_ok, "required": True, "detail": db_detail},
            "vector_index": {"ok": index_ok, "required": True, "detail": index_detail},
            "ollama": {"ok": ollama_ok, "required": False, "detail": ollama_detail},
            # 知識索引壞了只有知識題受影響（會回「目前無法使用」並自動重試），不擋整個服務
            "knowledge_index": {"ok": knowledge_ok, "required": False, "detail": knowledge_detail},
        },
    }


# 首頁
@app.get("/", response_class=FileResponse)
async def read_index():
    if not INDEX_FILE.is_file():
        raise HTTPException(status_code=404, detail="找不到 static/index.html")
    return FileResponse(INDEX_FILE, media_type="text/html")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8501)
