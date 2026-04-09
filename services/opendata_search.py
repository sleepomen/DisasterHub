"""
在政府資料開放平臺體系中，多數機關採 CKAN API。
預設可搜尋臺北市資料大平臺（穩定的 package_search JSON）；
若要搜尋其他節點，請設定環境變數 OPENDATA_CKAN_BASE。

【專案改動】新增本模組以支援「線上開放資料關鍵字搜尋」：由 app POST /api/opendata/search
與 MCP 工具 search_taiwan_open_data 呼叫，避免重複實作；選 CKAN 係因回傳結構化 JSON、
較爬蟲網頁穩定。
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import requests

DEFAULT_CKAN_BASE = "https://data.taipei"
REQUEST_TIMEOUT = 20


def search_open_data_packages(query: str, ckan_base: str | None = None, limit: int = 15) -> dict[str, Any]:
    """
    使用 CKAN package_search 搜尋資料集。
    回傳為可 JSON 序列化的 dict（成功或失敗訊息）。
    """
    base = (ckan_base or os.environ.get("OPENDATA_CKAN_BASE") or DEFAULT_CKAN_BASE).rstrip("/")
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "搜尋關鍵字不可為空"}

    url = f"{base}/api/3/action/package_search"
    try:
        r = requests.get(
            url,
            params={"q": q, "rows": min(limit, 50)},
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "DisasterHub/1.0 (opendata search)"},
        )
        r.raise_for_status()
        payload = r.json()
    except requests.RequestException as e:
        return {"ok": False, "error": str(e), "ckan_base": base}

    if not payload.get("success"):
        return {"ok": False, "error": payload.get("error", {}), "ckan_base": base}

    results = (payload.get("result") or {}).get("results") or []
    items: list[dict[str, Any]] = []
    for p in results:
        name = p.get("name") or ""
        title = p.get("title") or ""
        notes = (p.get("notes") or "")[:280]
        landing = ""
        extras = p.get("url") or ""
        if extras:
            landing = extras
        elif name:
            landing = f"{base}/dataset/{quote(name)}"
        items.append(
            {
                "title": title,
                "name": name,
                "notes": notes,
                "url": landing,
            }
        )

    return {
        "ok": True,
        "ckan_base": base,
        "query": q,
        "count": len(items),
        "datasets": items,
    }


def format_search_results_for_prompt(result: dict[str, Any]) -> str:
    """供 LLM 或 MCP 工具回傳純文字。"""
    if not result.get("ok"):
        return f"開放資料搜尋失敗：{result.get('error', '未知錯誤')}"

    lines: list[str] = [
        f"搜尋節點：{result.get('ckan_base')}",
        f"關鍵字：{result.get('query')}",
        f"筆數：{result.get('count', 0)}",
        "",
    ]
    for i, d in enumerate(result.get("datasets") or [], 1):
        title = d.get("title") or d.get("name") or "（無標題）"
        url = d.get("url") or ""
        notes = d.get("notes") or ""
        lines.append(f"{i}. {title}")
        if url:
            lines.append(f"   連結：{url}")
        if notes:
            lines.append(f"   摘要：{notes}")
        lines.append("")
    return "\n".join(lines).strip()
