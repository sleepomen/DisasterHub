import asyncio
import contextvars
import json
import logging
import re
import secrets
import threading
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.datastructures import MutableHeaders

import config
from repositories.shelter_repository import ShelterRepository
from services import auth, health
from services.chat_service import ChatService
from services.map_service import MapService
from services.metrics import metrics
from services.population_service import PopulationModel
from services.rate_limit import RateLimiter
from services.sync_service import DataSyncService
from services.vector_store import VectorStore

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

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
INDEX_FILE = STATIC_DIR / "index.html"

# 1. 初始化 server / repo / services
repo = ShelterRepository()
map_service = MapService()
vector_store = VectorStore()
chat_service = ChatService(vector_store=vector_store, repo=repo)
population_model = PopulationModel()
session_manager = auth.SessionManager()
login_throttle = auth.LoginThrottle()
# LLM 生成的併發名額：一次生成最長 OLLAMA_TIMEOUT 秒，超出名額的問題立刻回 429，
# 不排隊、不佔執行緒，地圖與 readiness 才不會跟著 Ollama 一起卡住
chat_slots = threading.BoundedSemaphore(config.CHAT_MAX_CONCURRENT)
AI_BUSY = "AI 助手正在回答其他問題，請稍後再試。"
# 單一來源的用量上限。/api/chat 不需要登入（災時任何人都要問得到），
# 但也因此沒有任何東西擋住有人拿它當免費 LLM 用，所以按來源限流
chat_limiter = RateLimiter(config.CHAT_RATE_LIMIT, config.CHAT_RATE_WINDOW)
# 被擋掉的請求也要留下數字：rate_limit 偏高代表有人在刷，busy 偏高代表生成名額不夠用，
# 兩者的處理方式完全不同，混在一個 429 裡看不出來
metrics.register("chat.rejected.rate_limit", "chat.rejected.busy")

# 列舉題（一個縣 20 至 30 筆）生成要 1 分鐘以上，一次給完的話使用者只能看著轉圈等，
# 所以改用 SSE 邊生成邊送。no-cache / X-Accel-Buffering 是給前面的反向代理看的：
# nginx 預設會把整個回應緩衝起來再轉出，串流會整段失效
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def sync_and_reindex(force: bool = False) -> int:
    repo.ensure_schema()
    DataSyncService().sync()
    shelters = repo.get_all_shelters()
    vector_store.build_index(shelters, force=force)
    return len(shelters)


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


# 啟動同步失敗後的重試間隔（秒）。資料庫還沒起來、Ollama 還在載 embedding 模型都是
# 幾十秒就會好的暫時狀況，但索引空著 readiness 就一直回 503，容器也一直 unhealthy。
# 自己退避重試，不要求人剛好在線上去呼叫 /api/sync
STARTUP_RETRY_DELAYS = (5, 15, 30, 60, 120, 300)


async def retry_startup_sync() -> None:
    for delay in STARTUP_RETRY_DELAYS:
        await asyncio.sleep(delay)
        try:
            count = await asyncio.to_thread(sync_and_reindex)
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
        count = await asyncio.to_thread(sync_and_reindex)
        logger.info("啟動完成，共載入 %d 筆避難所", count)
    except Exception:
        logger.exception("啟動同步失敗，服務仍會啟動，接下來會在背景自動重試")
        retry_task = asyncio.create_task(retry_startup_sync())
    yield
    if retry_task is not None:
        retry_task.cancel()
        with suppress(asyncio.CancelledError):
            await retry_task


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


app = FastAPI(lifespan=lifespan)
app.add_middleware(RequestIdMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# liveness：只表示 process 還活著
@app.get("/health")
async def health_check():
    return {"status": "ok"}


# readiness：真的去戳依賴。
# 資料庫與向量索引重啟後可以自行恢復，列為必要條件；
# Ollama 跑在宿主機，重啟容器救不了它，只回報狀態不影響判定。
@app.get("/health/ready")
async def readiness_check(response: Response):
    db_ok, db_detail = await asyncio.to_thread(health.check_database, repo)
    index_ok, index_detail = await asyncio.to_thread(health.check_index, vector_store)
    ollama_ok, ollama_detail = await asyncio.to_thread(health.check_ollama)

    ready = db_ok and index_ok
    if not ready:
        response.status_code = 503

    return {
        "status": "ready" if ready else "not_ready",
        "checks": {
            "database": {"ok": db_ok, "required": True, "detail": db_detail},
            "vector_index": {"ok": index_ok, "required": True, "detail": index_detail},
            "ollama": {"ok": ollama_ok, "required": False, "detail": ollama_detail},
        },
    }

# Pydantic Models
#規範輸入範圍
class SimulateRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90, description="緯度")
    lon: float = Field(..., ge=-180, le=180, description="經度")
    radius: float = Field(..., gt=0, le=100, description="半徑（公里），最大100km")
    type: str = Field(default="earthquake")

    @field_validator("type")
    @classmethod
    def validate_type(cls, v):
        allowed = {"earthquake", "flood", "fire"}
        if v not in allowed:
            raise ValueError(f"災害類型必須是 {allowed} 其中之一")
        return v

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500, description="使用者問題")

class OccupancyItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    current_ppl: int = Field(..., ge=0, le=1_000_000, description="目前收容人數")


class OccupancyRequest(BaseModel):
    occupancy: list[OccupancyItem] = Field(..., min_length=1, max_length=1000)


class NearestRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90, description="緯度")
    lon: float = Field(..., ge=-180, le=180, description="經度")
    limit: int = Field(default=5, ge=1, le=20, description="回傳筆數")


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=200)


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


def current_user(http_request: Request) -> str | None:
    return session_manager.verify(http_request.cookies.get(auth.COOKIE_NAME))


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


@app.post("/api/login")
async def login(request: LoginRequest, http_request: Request, response: Response):
    if not auth.credentials_configured():
        raise HTTPException(status_code=503, detail="伺服器未設定 ADMIN_USERNAME / ADMIN_PASSWORD，登入已停用")
    key = client_key(http_request)
    wait = login_throttle.retry_after(key)
    if wait > 0:
        raise HTTPException(status_code=429, detail=f"登入失敗次數過多，請 {wait} 秒後再試")
    if not auth.check_credentials(request.username, request.password):
        login_throttle.record_failure(key)
        # 不記輸入的帳號：操作員把密碼打進帳號欄時，密碼就會留在 log 裡
        logger.warning("登入失敗：來源 %s", key)
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")
    login_throttle.reset(key)
    response.set_cookie(
        key=auth.COOKIE_NAME,
        value=session_manager.issue(request.username),
        max_age=session_manager.max_age,
        httponly=True,
        samesite="lax",
        secure=request_is_https(http_request),
        path="/",
    )
    logger.info("登入成功：%s，來源 %s", request.username, key)
    return {"status": "success", "user": request.username, "expires_in": session_manager.max_age}


@app.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie(key=auth.COOKIE_NAME, path="/")
    return {"status": "success"}


# 前端載入時問一次：要不要顯示登入面板、目前是誰
@app.get("/api/me")
async def me(http_request: Request):
    user = current_user(http_request)
    return {
        "authenticated": user is not None,
        "user": user,
        "login_enabled": auth.credentials_configured(),
    }


#sync_service.sync() 讀取json檔案寫入pgSQL
#vector_store.build_index()重建chromadb向量索引
@app.post("/api/sync")
async def manual_sync(http_request: Request, x_api_key: str = Header(default="")):
    require_sync_access(http_request, x_api_key)
    count = await asyncio.to_thread(sync_and_reindex, True)
    return {"status": "success", "message": "資料同步與索引重建完成", "count": count}

# 地圖載入時呼叫
#去pgSQL拿所有避難所資料
#shelter 物件轉成前端需要的 json 格式
@app.get("/api/shelters")
async def get_shelters():
    shelters = await asyncio.to_thread(repo.get_all_shelters)
    data = map_service.to_map_points(shelters)
    return data

#執行空間模擬時呼叫
#repository 對 postGIS 執行 ST_DWithin 空間查詢
#回傳影響範圍清單給前端
#同時將模擬結果交給 chat_service 供 AI 聊天使用
#模擬會覆蓋全域的聊天快照，所以跟寫入端點一樣要先登入
@app.post("/api/simulate_disaster")
async def simulate(request: SimulateRequest, http_request: Request, x_api_key: str = Header(default="")):
    require_write_access(http_request, x_api_key)
    impacted = await asyncio.to_thread(
        repo.get_shelters_in_radius, request.lat, request.lon, request.radius
    )
    # 人口估算與前端動畫、AI 回答共用同一份模型，三邊數字才會一致
    total_remaining = sum(s["remaining"] for s in impacted)
    population = population_model.estimate(
        request.lat, request.lon, request.radius, request.type, total_remaining
    )

    chat_service.set_simulation({
        "type": request.type,
        "lat": request.lat,
        "lon": request.lon,
        "radius_km": request.radius,
        "impacted_count": len(impacted),
        "impacted_shelters": impacted,
        "population": population,
    })

    return {
        "status": "success",
        "impacted_count": len(impacted),
        "impacted_shelters": impacted,
        "population": population,
    }


# 人口模型（海岸線 + 鄉鎮人口）：前端人群動畫用它決定人從哪裡出發
@app.get("/api/population")
async def get_population():
    return population_model.to_dict()

# 疏散動畫結束後由前端回寫各避難所的收容人數。
# 沒有這一步，地圖上滿載變紅的避難所在資料庫裡仍是 0 人，AI 會回答跟畫面相反的結論。
@app.post("/api/occupancy")
async def update_occupancy(request: OccupancyRequest, http_request: Request, x_api_key: str = Header(default="")):
    require_write_access(http_request, x_api_key)
    occupancy = {item.name: item.current_ppl for item in request.occupancy}
    try:
        result = await asyncio.to_thread(apply_occupancy, occupancy)
    except Exception:
        logger.exception("收容人數回寫失敗")
        raise HTTPException(status_code=500, detail="收容人數回寫失敗") from None
    return {"status": "success", **result}


# 清除模擬：把收容人數還原成來源資料的初始值，資料庫、向量索引、聊天快照一起歸零
@app.post("/api/reset_simulation")
async def reset_simulation(http_request: Request, x_api_key: str = Header(default="")):
    require_write_access(http_request, x_api_key)
    try:
        baseline = await asyncio.to_thread(DataSyncService().baseline_occupancy)
        result = await asyncio.to_thread(apply_occupancy, baseline)
    except Exception:
        logger.exception("重置收容人數失敗")
        raise HTTPException(status_code=500, detail="重置收容人數失敗") from None
    # 資料庫真的還原了才清快照；先清再寫失敗會變成聊天說沒模擬、地圖與資料庫卻還是滿載
    chat_service.clear_simulation()
    return {"status": "success", **result}

# 最近避難所查詢
# 使用 PostGIS ST_Distance 真實地理距離排序
# 解決 RAG 語意搜尋無法處理「最近/附近」等地理問題
@app.post("/api/nearest_shelter")
async def nearest_shelter(request: NearestRequest):
    results = await asyncio.to_thread(
        repo.get_nearest_shelters, request.lat, request.lon, request.limit
    )
    return {
        "status": "success",
        "count": len(results),
        "shelters": results
    }

#呼叫 chat_service.chat_stream() 傳入問題
#chatservice依問題類型查 DB / chromadb，組合 prompt 後透過 http 呼叫 ollama
#llm 的回答以 SSE 逐段送回前端，每個事件是一個 {"type": "delta"|"error"|"done", "text": ...}
@app.post("/api/chat")
async def chat(request: ChatRequest, http_request: Request):
    # 來源用量先看：被限流的請求不該佔用生成名額
    wait = chat_limiter.hit(client_key(http_request))
    if wait > 0:
        metrics.incr("chat.rejected.rate_limit")
        raise HTTPException(
            status_code=429,
            detail=f"提問太頻繁，請 {wait} 秒後再試。",
            headers={"Retry-After": str(wait)},
        )
    # 名額要在開始串流前就拿到，拿不到才有機會回 429；
    # 串流一開始送出，狀態碼就定了，之後的錯誤只能以 error 事件表達
    if not chat_slots.acquire(blocking=False):
        metrics.incr("chat.rejected.busy")
        raise HTTPException(status_code=429, detail=AI_BUSY)

    def stream():
        # 同步產生器由 Starlette 丟到執行緒池逐段取，阻塞的 requests 呼叫不會卡住事件迴圈。
        # 名額撐到整段結束（含使用者中途關掉頁面）才還，否則併發上限等於沒設
        try:
            for event in chat_service.chat_stream(request.message):
                yield sse_event(event)
            yield sse_event({"type": "done"})
        finally:
            chat_slots.release()

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)

# 線上計數器。權限跟寫入端點一樣：它會揭露流量模式與節流狀態，不該對外公開
# （對外的存活/就緒檢查用 /health 與 /health/ready）
@app.get("/api/stats")
async def stats(http_request: Request, x_api_key: str = Header(default="")):
    require_write_access(http_request, x_api_key)
    return metrics.snapshot()


# 渲染首頁
@app.get("/", response_class=FileResponse)
async def read_index():
    if not INDEX_FILE.is_file():
        raise HTTPException(status_code=404, detail="找不到 static/index.html")
    return FileResponse(INDEX_FILE, media_type="text/html")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8501)
