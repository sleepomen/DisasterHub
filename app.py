import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from repositories.shelter_repository import ShelterRepository
from services.map_service import MapService
from services.sync_service import DataSyncService
from services.chat_service import ChatService
from services.vector_store import VectorStore
import config
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# 1. 初始化 server / repo / services
repo = ShelterRepository()
map_service = MapService()
vector_store = VectorStore()
chat_service = ChatService(vector_store=vector_store, repo=repo)


def sync_and_reindex() -> int:
    repo.ensure_schema()
    DataSyncService().sync()
    shelters = repo.get_all_shelters()
    vector_store.build_index(shelters)
    return len(shelters)


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
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health")
async def health():
    return {"status": "ok"}

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

class NearestRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90, description="緯度")
    lon: float = Field(..., ge=-180, le=180, description="經度")
    limit: int = Field(default=5, ge=1, le=20, description="回傳筆數")

#sync_service.sync() 讀取json檔案寫入pgSQL
#vector_store.build_index()重建chromadb向量索引
@app.post("/api/sync")
async def manual_sync(x_api_key: str = Header(default="")):
    if not config.SYNC_API_KEY:
        raise HTTPException(status_code=503, detail="伺服器未設定 SYNC_API_KEY，手動同步已停用")
    if not secrets.compare_digest(x_api_key, config.SYNC_API_KEY):
        raise HTTPException(status_code=401, detail="API key 無效")
    count = await asyncio.to_thread(sync_and_reindex)
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
@app.post("/api/simulate_disaster")
async def simulate(request: SimulateRequest):
    impacted = await asyncio.to_thread(
        repo.get_shelters_in_radius, request.lat, request.lon, request.radius
    )

    chat_service.set_simulation({
        "type": request.type,
        "lat": request.lat,
        "lon": request.lon,
        "radius_km": request.radius,
        "impacted_count": len(impacted),
        "impacted_shelters": impacted
    })

    return {
        "status": "success",
        "impacted_count": len(impacted),
        "impacted_shelters": impacted
    }

@app.post("/api/reset_simulation")
async def reset_simulation():
    chat_service.clear_simulation()
    return {"status": "success"}

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
    reply = await asyncio.to_thread(chat_service.chat, request.message)
    return {"status": "success", "reply": reply}

# 渲染首頁
@app.get("/", response_class=HTMLResponse)
async def read_index():
    try:
        with open("static/index.html", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>Static/index.html 檔案不存在</h1>"

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8501)
