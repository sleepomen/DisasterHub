"""
容量缺口報告的網頁版。報告由 tools/capacity_report.py 產生成 reports/capacity_gap.json，這裡只讀檔，
不在請求裡重算：116 個情境要 70 秒，而且紅色警戒情境會暫時改動網站上的路線。不需要登入
"""
import json
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from services.simulation.capacity_report import page_view

ROOT = Path(__file__).resolve().parent.parent
REPORT_FILE = ROOT / "reports" / "capacity_gap.json"
PAGE_FILE = ROOT / "static" / "report.html"

router = APIRouter()
_lock = threading.Lock()
_cache: dict = {"mtime": None, "view": None}


def load_view(path: Path | None = None) -> dict | None:
    """報告檔 → 網頁要的部分。檔案沒變就用上一次的結果；沒有檔案回 None"""
    path = path or REPORT_FILE
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return None
    with _lock:
        if _cache["mtime"] != (path, mtime):
            _cache["view"] = page_view(json.loads(path.read_text(encoding="utf-8")))
            _cache["mtime"] = (path, mtime)
        return _cache["view"]


@router.get("/report", response_class=FileResponse)
async def report_page():
    return FileResponse(PAGE_FILE, media_type="text/html")


@router.get("/api/capacity_report")
async def capacity_report():
    view = load_view()
    if view is None:
        raise HTTPException(status_code=404, detail="還沒有報告：先執行 python tools/capacity_report.py")
    return view
