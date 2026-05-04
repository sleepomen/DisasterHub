from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator
from repositories.shelter_repository import ShelterRepository
from services.map_server import MapService
from services.sync_service import DataSyncService
from services.chat_service import ChatService
from services.vector_store import VectorStore
from services.disaster_feed_service import DisasterFeedService
from services.opendata_search import format_search_results_for_prompt, search_open_data_packages
import uvicorn

app = FastAPI()

# 1. 初始化 server / repo / services
repo = ShelterRepository()
map_service = MapService()
vector_store = VectorStore()
chat_service = ChatService(vector_store=vector_store, repo=repo)

# 【擴充】即時公開災害摘要：氣象署 E-A0015-001（環境變數 CWA_AUTHORIZATION）與 RSS／Atom 清單
# （DISASTER_FEED_URLS）。供 GET /api/disaster_feed、前端輪詢顯示於聊天框，並在 POST /api/chat
# 注入 LLM；邏輯集中在 services/disaster_feed_service，與 mcp_server 共用。
disaster_feeds = DisasterFeedService()

# 儲存最新模擬結果（記憶體暫存）
latest_simulation: dict = {}

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

# 【擴充】開放資料搜尋請求本體：對應 POST /api/opendata/search，後端呼叫 CKAN package_search
# （預設節點 https://data.taipei，可改 OPENDATA_CKAN_BASE）。
class OpenDataSearchRequest(BaseModel):
    q: str = Field(..., min_length=1, max_length=120, description="關鍵字")
    limit: int = Field(default=12, ge=1, le=30)

# 跟/api/sync一起進行資料同步和重建索引
@app.on_event("startup")
async def startup_sync():
    print("啟動時執行資料同步...")
    sync_service = DataSyncService()
    sync_service.sync()

    print("建立向量索引...")
    shelters = repo.get_all_shelters()
    vector_store.build_index(shelters)

#sync_service.sync() 讀取json檔案寫入pgSQL
#vector_store.build_index()重建chromadb向量索引
@app.post("/api/sync")
async def manual_sync():
    sync_service = DataSyncService()
    sync_service.sync()
    shelters = repo.get_all_shelters()
    vector_store.build_index(shelters)
    return {"status": "success", "message": "資料同步與索引重建完成"}

# 地圖載入時呼叫
#去pgSQL拿所有避難所資料
#shelter 物件轉成前端需要的 json 格式
@app.get("/api/3d_data")
async def get_3d_data():
    shelters = repo.get_all_shelters()
    data = map_service.prepare_3d_data(shelters)
    return data

#執行空間模擬時呼叫
#repository 對 postGIS 執行 ST_DWithin 空間查詢
#回傳影響範圍清單給前端
#同時將模擬結果存入記憶體供 AI 聊天使用
@app.post("/api/simulate_disaster")
async def simulate(request: SimulateRequest):
    global latest_simulation

    impacted = repo.get_shelters_in_radius(request.lat, request.lon, request.radius)

    latest_simulation = {
        "type": request.type,
        "lat": request.lat,
        "lon": request.lon,
        "radius_km": request.radius,
        "impacted_count": len(impacted),
        "impacted_shelters": impacted
    }

    # 【修正】以下兩行須在此函式內執行，否則模擬後無法更新 ChatService 狀態亦無法正確回傳。
    chat_service.set_simulation(latest_simulation)

    return {
        "status": "success",
        "impacted_count": len(impacted),
        "impacted_shelters": impacted
    }

# 最近避難所查詢
# 使用 PostGIS ST_Distance 真實地理距離排序
# 解決 RAG 語意搜尋無法處理「最近/附近」等地理問題
@app.post("/api/nearest_shelter")
async def nearest_shelter(request: NearestRequest):
    results = repo.get_nearest_shelters(request.lat, request.lon, request.limit)
    return {
        "status": "success",
        "count": len(results),
        "shelters": results
    }

#讀取latest_simulation
#呼叫 chat_service.chat() 傳入問題/模擬context
#chatservice對chromadb進行語意搜尋
#資料/模擬結果/用戶問題組合成prompt給llm
#透過http呼叫ollama
#llm回答回傳前端
@app.post("/api/chat")
async def chat(request: ChatRequest):
    sim_context = ""
    if latest_simulation:
        sim_context = (
            f"災害類型：{latest_simulation['type']}，"
            f"中心座標：({latest_simulation['lat']}, {latest_simulation['lon']})，"
            f"影響半徑：{latest_simulation['radius_km']} 公里，"
            f"受影響避難所數量：{latest_simulation['impacted_count']} 個。"
        )

    # 【擴充】將與前端 /api/disaster_feed 同源之摘要併入 prompt，使 AI 與聊天框通報語境一致。
    feed_text = disaster_feeds.get_summary_for_chat()
    reply = chat_service.chat(
        request.message,
        simulation_context=sim_context,
        live_feed_context=feed_text,
    )
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