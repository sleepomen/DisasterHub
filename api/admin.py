"""手動同步資料、看線上計數器：給操作員用的端點，都要登入或帶金鑰"""
import asyncio

from fastapi import APIRouter, Header, Request

from api import deps
from services.web.metrics import metrics

router = APIRouter()


# 重新讀來源資料寫進資料庫，再重建向量索引與知識索引
@router.post("/api/sync")
async def manual_sync(http_request: Request, x_api_key: str = Header(default="")):
    deps.require_sync_access(http_request, x_api_key)
    count = await asyncio.to_thread(deps.sync_and_reindex, True)
    return {"status": "success", "message": "資料同步與索引重建完成", "count": count}


# 線上計數器。權限跟寫入端點一樣：它會揭露流量模式與節流狀態，不該對外公開
# （對外的存活/就緒檢查用 /health 與 /health/ready）
@router.get("/api/stats")
async def stats(http_request: Request, x_api_key: str = Header(default="")):
    deps.require_write_access(http_request, x_api_key)
    return metrics.snapshot()
