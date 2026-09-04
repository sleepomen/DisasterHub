import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Response
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
import config
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# 設定不完整就不要假裝服務正常，直接在 import 階段失敗，訊息才看得懂
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

#sync_service.sync() 讀取json檔案寫入pgSQL
#vector_store.build_index()重建chromadb向量索引
@app.post("/api/sync")
async def manual_sync(x_api_key: str = Header(default="")):
    if not config.SYNC_API_KEY:
        raise HTTPException(status_code=503, detail="伺服器未設定 SYNC_API_KEY，手動同步已停用")
    if not secrets.compare_digest(x_api_key, config.SYNC_API_KEY):
        raise HTTPException(status_code=401, detail="API key 無效")
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
@app.post("/api/simulate_disaster")
async def simulate(request: SimulateRequest):
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
async def update_occupancy(request: OccupancyRequest):
    occupancy = {item.name: item.current_ppl for item in request.occupancy}
    try:
        result = await asyncio.to_thread(apply_occupancy, occupancy)
    except Exception:
        logger.exception("收容人數回寫失敗")
        raise HTTPException(status_code=500, detail="收容人數回寫失敗")
    return {"status": "success", **result}


# 清除模擬：把收容人數還原成來源資料的初始值，資料庫、向量索引、聊天快照一起歸零
@app.post("/api/reset_simulation")
async def reset_simulation():
    chat_service.clear_simulation()
    try:
        baseline = await asyncio.to_thread(DataSyncService().baseline_occupancy)
        result = await asyncio.to_thread(apply_occupancy, baseline)
    except Exception:
        logger.exception("重置收容人數失敗")
        raise HTTPException(status_code=500, detail="重置收容人數失敗")
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
    reply = await asyncio.to_thread(chat_service.chat, request.message)
    return {"status": "success", "reply": reply}

# 渲染首頁
@app.get("/", response_class=FileResponse)
async def read_index():
    if not INDEX_FILE.is_file():
        raise HTTPException(status_code=404, detail="找不到 static/index.html")
    return FileResponse(INDEX_FILE, media_type="text/html")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8501)
