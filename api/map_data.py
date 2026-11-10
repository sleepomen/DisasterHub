"""地圖要的資料：避難所、潛勢範圍、即時警戒、人口模型、選點名稱、最近避難所。都不需要登入"""
import asyncio

from fastapi import APIRouter, Query

import config
from api import deps
from api.models import NearestRequest
from services.geo.routing import nearest_by_road
from services.web.i18n import place_en

router = APIRouter()


# 地圖載入時呼叫：資料庫裡所有避難所，轉成前端要的格式
@router.get("/api/shelters")
async def get_shelters():
    shelters = await asyncio.to_thread(deps.repo.get_all_shelters)
    return deps.map_service.to_map_points(shelters)


# 災害潛勢區域（GeoJSON），地圖疊圖用。檔案在啟動時讀一次就好，內容只有重建資料時才會變
@router.get("/api/hazard_zones")
async def get_hazard_zones():
    return deps.hazard_zones_geojson()


# 即時土石流警戒：哪些潛勢溪流發布了黃色或紅色警戒，以及範圍內有多少道路被封閉或要繞行
@router.get("/api/alerts")
async def get_alerts():
    alerts = await asyncio.to_thread(deps.alert_summary)
    return {"alerts": alerts, "poll_seconds": config.ALERT_POLL_SECONDS}


# 人口模型（海岸線 + 由村里加總的鄉鎮人口）
@router.get("/api/population")
async def get_population():
    return deps.population_model.to_dict()


# 地圖上點選的模擬中心是哪裡：最近的村里與鄉鎮（只是標籤），以及在不在陸地上
@router.get("/api/locate")
async def locate(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180)):
    place = deps.population_model.locate(lat, lon)
    return {**place, "township_en": place_en(place["township"]) if place["township"] else "",
            "village_en": place_en(f"{place['township']}{place['village']}") if place["village"] else ""}


# 最近避難所：先用 PostGIS 直線距離挑候選，再依 pgRouting 的道路距離排序；路網算不出來時退回直線距離
@router.post("/api/nearest_shelter")
async def nearest_shelter(request: NearestRequest):
    results = await asyncio.to_thread(nearest_by_road, deps.repo, request.lat, request.lon, request.limit, request.mode)
    return {
        "status": "success",
        "count": len(results),
        "shelters": deps.map_service.to_nearest(results),
    }
