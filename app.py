import asyncio
import logging
import secrets
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from repositories.shelter_repository import ShelterRepository
from services.map_service import MapService
from services.sync_service import DataSyncService
from services.chat_service import ChatService
from services.vector_store import VectorStore
from services.population_service import PopulationModel
from services import health
from services import auth
import config
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("啟動時執行資料同步與向量索引建立...")
    try:
        count = await asyncio.to_thread(sync_and_reindex)
        logger.info("啟動完成，共載入 %d 筆避難所", count)
    except Exception:
        logger.exception("啟動同步失敗，服務仍會啟動，可稍後呼叫 /api/sync 重試")
    yield


app = FastAPI(lifespan=lifespan)
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


def require_api_key(provided: str, expected: str, setting: str, feature: str) -> None:
    """
    只靠金鑰的端點（/api/sync）：伺服器沒設金鑰就整個停用（503）而不是放行，
    設定漏掉時才不會變成人人可寫。
    """
    if not expected:
        raise HTTPException(status_code=503, detail=f"伺服器未設定 {setting}，{feature}已停用")
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="API key 無效")


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
async def manual_sync(x_api_key: str = Header(default="")):
    require_api_key(x_api_key, config.SYNC_API_KEY, "SYNC_API_KEY", "手動同步")
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
        raise HTTPException(status_code=500, detail="收容人數回寫失敗")
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
        raise HTTPException(status_code=500, detail="重置收容人數失敗")
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

#呼叫 chat_service.chat() 傳入問題
#chatservice依問題類型查 DB / chromadb，組合 prompt 後透過 http 呼叫 ollama
#llm回答回傳前端
@app.post("/api/chat")
async def chat(request: ChatRequest):
    if not chat_slots.acquire(blocking=False):
        raise HTTPException(status_code=429, detail=AI_BUSY)
    try:
        reply = await asyncio.to_thread(chat_service.chat, request.message)
    finally:
        chat_slots.release()
    return {"status": "success", "reply": reply}

# 渲染首頁
@app.get("/", response_class=FileResponse)
async def read_index():
    if not INDEX_FILE.is_file():
        raise HTTPException(status_code=404, detail="找不到 static/index.html")
    return FileResponse(INDEX_FILE, media_type="text/html")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8501)
