"""
執行模擬、回寫收容人數、清除模擬。三個都會改資料庫與全域的聊天快照，
上線後只要連得到網址就能直接呼叫，所以都要先登入（或帶寫入金鑰）
"""
import asyncio
import functools
import logging

from fastapi import APIRouter, Header, HTTPException, Request

from api import deps
from api.models import OccupancyRequest, SimulateRequest
from services.geo.routing import attach_evacuation_paths, evacuation_road_distances, solve_assignment
from services.ingest.sync import DataSyncService
from services.simulation.evacuation import (
    DESIGNATED_HAZARD,
    EXCLUDED_HAZARDS,
    HAZARD_WARN_M,
    origins_from,
    plan_evacuation,
    split_hazard_hits,
    undesignated,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# 圈內的避難所（PostGIS ST_DWithin）、人口估算、疏散分配與路線；結果寫入資料庫，也交給聊天服務當快照
@router.post("/api/simulate_disaster")
async def simulate(request: SimulateRequest, http_request: Request, x_api_key: str = Header(default="")):
    deps.require_write_access(http_request, x_api_key)
    repo = deps.repo
    impacted = await asyncio.to_thread(
        repo.get_shelters_in_radius, request.lat, request.lon, request.radius
    )
    # 位在對應災害潛勢區域內的避難所不當疏散目的地（淹水模擬避開土石流影響範圍）。
    # 範圍外 HAZARD_WARN_M 公尺內的照常分配，只附上警告。
    # 查不到潛勢資料時退回「不排除」，跟沒有這個功能時一樣，模擬本身不能因此失敗
    excluded: dict[str, list[str]] = {}
    near: dict[str, dict] = {}
    hazard = EXCLUDED_HAZARDS.get(request.type)
    if hazard:
        try:
            hits = await asyncio.to_thread(repo.shelter_hazard_distances, hazard, HAZARD_WARN_M)
            inside, nearby = split_hazard_hits(hits)
            excluded = {s.name: inside[s.name] for s in impacted if s.name in inside}
            near = {s.name: nearby[s.name] for s in impacted if s.name in nearby}
        except Exception:
            logger.exception("查詢災害潛勢區域失敗，本次模擬不排除任何避難所")
    # 消防署資料沒把這間列為這類災害的避難收容處所（例如只列水災的，地震時不去）
    not_designated = undesignated(impacted, request.type)
    destinations = [s for s in impacted if s.name not in excluded and s.name not in not_designated]

    # 人口估算與前端動畫、AI 回答共用同一份模型，三邊數字才會一致；
    # 可安置空間只算真的能去的避難所，否則「可安置」會跟分配結果對不上
    total_remaining = sum(s.remaining for s in destinations)
    population = deps.population_model.estimate(
        request.lat, request.lon, request.radius, request.type, total_remaining
    )
    # 誰去哪一間由後端決定，結果確定、可重現；前端只把這份分配畫成動畫。
    # 以前是瀏覽器用亂數分配、動畫跑完才回寫，AI 要等回寫成功才看得到同樣的數字。
    # 每個受影響的村里一個起點；分配讓所有人的通行時間加總最短（最小成本流，在資料庫裡解），
    # 時間依路網計算，路網查不到時退回直線距離。路線繞開警戒範圍（紅色封閉、黃色繞行），淹水模擬另外繞開土石流潛勢範圍
    origins = origins_from(population, request.lat, request.lon)
    road_m = await asyncio.to_thread(evacuation_road_distances, repo, origins, destinations, hazard, request.mode)
    plan = await asyncio.to_thread(
        plan_evacuation, origins, destinations, road_m, request.mode, functools.partial(solve_assignment, repo),
    )
    # 實際路線用來畫圖、回報實際距離；分配已經決定好了，查不到就畫直線，不影響人數
    await asyncio.to_thread(attach_evacuation_paths, repo, origins, destinations, plan["flows"], hazard, request.mode)
    plan["route_hazard"] = hazard
    plan["alerts"] = await asyncio.to_thread(deps.alert_summary)
    plan["excluded"] = excluded
    plan["near_hazard"] = near
    plan["hazard"] = hazard if excluded or near else None
    plan["undesignated"] = not_designated
    plan["designation"] = DESIGNATED_HAZARD.get(request.type)

    deps.chat_service.set_simulation({
        "type": request.type,
        "lat": request.lat,
        "lon": request.lon,
        "radius_km": request.radius,
        "impacted_count": len(impacted),
        # 快照存 Shelter 物件：收容人數回寫後 refresh_occupancy 直接換成新的物件就好
        "impacted_shelters": impacted,
        "population": population,
        "evacuation": plan,
    })

    occupancy = {s.name: s.current_people + plan["assigned"][s.name] for s in impacted if s.name in plan["assigned"]}
    try:
        written = await asyncio.to_thread(deps.apply_occupancy, occupancy) if occupancy else None
    except Exception:
        # 資料庫沒寫進去就不能留著快照，否則 AI 會講一份資料庫裡不存在的分配
        deps.chat_service.clear_simulation()
        logger.exception("寫入疏散分配失敗")
        raise HTTPException(status_code=500, detail="寫入疏散分配失敗") from None

    return {
        "status": "success",
        "impacted_count": len(impacted),
        "impacted_shelters": deps.map_service.to_impacted(impacted),
        "population": population,
        "evacuation": plan,
        "occupancy": written["occupancy"] if written else None,
    }


# 疏散動畫結束後由前端回寫各避難所的收容人數。
# 沒有這一步，地圖上滿載變紅的避難所在資料庫裡仍是 0 人，AI 會回答跟畫面相反的結論。
@router.post("/api/occupancy")
async def update_occupancy(request: OccupancyRequest, http_request: Request, x_api_key: str = Header(default="")):
    deps.require_write_access(http_request, x_api_key)
    occupancy = {item.name: item.current_ppl for item in request.occupancy}
    try:
        result = await asyncio.to_thread(deps.apply_occupancy, occupancy)
    except Exception:
        logger.exception("收容人數回寫失敗")
        raise HTTPException(status_code=500, detail="收容人數回寫失敗") from None
    return {"status": "success", **result}


# 清除模擬：把收容人數還原成來源資料的初始值，資料庫、向量索引、聊天快照一起歸零
@router.post("/api/reset_simulation")
async def reset_simulation(http_request: Request, x_api_key: str = Header(default="")):
    deps.require_write_access(http_request, x_api_key)
    try:
        baseline = await asyncio.to_thread(DataSyncService().baseline_occupancy)
        result = await asyncio.to_thread(deps.apply_occupancy, baseline)
    except Exception:
        logger.exception("重置收容人數失敗")
        raise HTTPException(status_code=500, detail="重置收容人數失敗") from None
    # 資料庫真的還原了才清快照；先清再寫失敗會變成聊天說沒模擬、地圖與資料庫卻還是滿載
    deps.chat_service.clear_simulation()
    return {"status": "success", **result}
