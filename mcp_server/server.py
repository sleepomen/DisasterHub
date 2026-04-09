"""
Disaster Hub MCP Server（stdio）

在 Cursor 設定中可加入：
"command": "python",
"args": ["-m", "mcp_server"],
"cwd": "<本專案根目錄>"

環境變數與主程式相同：CWA_AUTHORIZATION、DISASTER_FEED_URLS、OPENDATA_CKAN_BASE、OPENDATA_SHELTER_CSV_URL 等。

獨立 MCP 服務透過 FastMCP 暴露三工具，內部僅 import services/opendata_search、
disaster_feed_service、shelter_remote_fetch，與 FastAPI 路由共用實作、不重複維護。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mcp.server.fastmcp import FastMCP

from services.disaster_feed_service import DisasterFeedService
from services.opendata_search import format_search_results_for_prompt, search_open_data_packages
from services.shelter_remote_fetch import load_shelters_from_csv_url, remote_shelter_url_from_env

mcp = FastMCP("disaster-hub")
_feeds = DisasterFeedService()


@mcp.tool()
def search_taiwan_open_data(query: str, limit: int = 12) -> str:
    """搜尋政府開放資料平臺體系之 CKAN 資料集（預設節點可透過 OPENDATA_CKAN_BASE 覆寫）。"""
    result = search_open_data_packages(query, limit=min(max(1, limit), 30))
    return format_search_results_for_prompt(result)


@mcp.tool()
def get_live_disaster_broadcast_summary() -> str:
    """取得即時公開災害資訊摘要（氣象署 API 與 RSS 訂閱需在環境變數設定）。"""
    return _feeds.get_summary_for_chat()


@mcp.tool()
def preview_remote_shelter_csv(url: str) -> str:
    """自指定 CSV 網址預覽將載入的避難所筆數與前 3 筆名稱（不寫入資料庫）。"""
    u = (url or "").strip() or remote_shelter_url_from_env()
    if not u:
        return "未提供網址，且環境變數 OPENDATA_SHELTER_CSV_URL 為空。"
    rows = load_shelters_from_csv_url(u)
    if not rows:
        return f"自 {u} 未能解析出避難所，請確認為 UTF-8／Big5 CSV 且含名稱與經緯度欄位。"
    preview = "\n".join(f"  - {s.name}" for s in rows[:3])
    return f"筆數：{len(rows)}\n前 3 筆：\n{preview}"


def main():
    mcp.run()


if __name__ == "__main__":
    main()
