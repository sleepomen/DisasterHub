"""
整合即時災害相關公開資訊來源：
- 中央氣象署 open data 顯著有感地震（需 CWA_AUTHORIZATION）
- 自訂 RSS／Atom（環境變數 DISASTER_FEED_URLS，逗號分隔）

供 REST、聊天 context、MCP 共用。

【專案改動】集中快取（CACHE_TTL_SEC）與摘要文字產生：GET /api/disaster_feed 供前端輪詢
寫入聊天框（去重 id）；get_summary_for_chat 供 /api/chat；get_live_disaster_broadcast_summary
供 MCP。內容均為公開資料彙整，非官方即時推播保證。
"""
from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
import hashlib

import requests

CWA_EARTHQUAKE_URL = (
    "https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0015-001"
    "?Authorization={key}&format=JSON&limit={limit}"
)

REQUEST_TIMEOUT = 25
CACHE_TTL_SEC = 90


def _env_feed_urls() -> list[str]:
    raw = os.environ.get("DISASTER_FEED_URLS", "").strip()
    if not raw:
        return []
    return [u.strip() for u in raw.split(",") if u.strip()]


def _parse_rss_or_atom(xml_text: str, source_label: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    if root.tag.endswith("feed") or root.find("atom:entry", ns) is not None:
        for entry in root.findall("atom:entry", ns) or root.findall("{http://www.w3.org/2005/Atom}entry"):
            title_el = entry.find("atom:title", ns) or entry.find("{http://www.w3.org/2005/Atom}title")
            link_el = entry.find("atom:link", ns) or entry.find("{http://www.w3.org/2005/Atom}link")
            updated_el = entry.find("atom:updated", ns) or entry.find("{http://www.w3.org/2005/Atom}updated")
            title = (title_el.text or "").strip() if title_el is not None and title_el.text else ""
            link = ""
            if link_el is not None:
                link = link_el.get("href") or ""
            pub = (updated_el.text or "").strip() if updated_el is not None and updated_el.text else ""
            if title:
                items.append(
                    {
                        "source": source_label,
                        "kind": "feed",
                        "title": title,
                        "link": link,
                        "published": pub,
                    }
                )
        return items

    for ch in root.findall(".//channel") or [root]:
        for item in ch.findall("item"):
            title_el = item.find("title")
            link_el = item.find("link")
            pub_el = item.find("pubDate")
            title = (title_el.text or "").strip() if title_el is not None and title_el.text else ""
            link = (link_el.text or "").strip() if link_el is not None and link_el.text else ""
            pub = (pub_el.text or "").strip() if pub_el is not None and pub_el.text else ""
            if title:
                items.append(
                    {
                        "source": source_label,
                        "kind": "feed",
                        "title": title,
                        "link": link,
                        "published": pub,
                    }
                )
    return items


def _fetch_cwa_earthquakes(limit: int = 8) -> list[dict[str, Any]]:
    key = (os.environ.get("CWA_AUTHORIZATION") or "").strip()
    if not key:
        return []
    url = CWA_EARTHQUAKE_URL.format(key=requests.utils.quote(key, safe=""), limit=limit)
    try:
        r = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "DisasterHub/1.0 (CWA)"},
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[disaster_feed] CWA API 失敗：{e}")
        return []

    records = data.get("records") or {}
    eq_block = records.get("Earthquake") or records.get("earthquake") or []
    if isinstance(eq_block, dict):
        eq_block = [eq_block]

    out: list[dict[str, Any]] = []
    for rec in eq_block:
        info = rec.get("earthquakeInfo") or rec
        if not isinstance(info, dict):
            continue
        origin = info.get("originTime", "")
        epic = info.get("epicenter") or {}
        loc = epic.get("location", "") if isinstance(epic, dict) else ""
        mag_el = info.get("magnitude") if isinstance(info, dict) else None
        mag = ""
        if isinstance(mag_el, dict):
            mag = str(mag_el.get("magnitudeValue", ""))

        lon = epic.get("epicenterLon", "") if isinstance(epic, dict) else ""
        lat = epic.get("epicenterLat", "") if isinstance(epic, dict) else ""
        title = f"地震報告 規模{mag}" if mag else "地震報告"
        if loc:
            title = f"{title} · {loc}".strip()

        out.append(
            {
                "source": "中央氣象署有感地震",
                "kind": "cwa_earthquake",
                "title": title,
                "link": "",
                "published": str(origin),
                "detail": f"經度 {lon} 緯度 {lat} 時間 {origin}",
            }
        )
    return out


class DisasterFeedService:
    def __init__(self):
        self._cache_items: list[dict[str, Any]] | None = None
        self._cache_at: float = 0.0

    def _refresh_locked(self) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        merged.extend(_fetch_cwa_earthquakes())

        for u in _env_feed_urls():
            label = u.split("/")[2] if "://" in u else u
            try:
                r = requests.get(
                    u,
                    timeout=REQUEST_TIMEOUT,
                    headers={"User-Agent": "DisasterHub/1.0 (RSS)"},
                )
                r.raise_for_status()
                encoding = r.encoding or "utf-8"
                text = r.content.decode(encoding, errors="replace")
                merged.extend(_parse_rss_or_atom(text, label))
            except Exception as e:
                print(f"[disaster_feed] RSS 失敗 {u}: {e}")

        self._cache_items = merged
        self._cache_at = time.monotonic()
        return merged

    def get_items(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._cache_items is not None and (now - self._cache_at) < CACHE_TTL_SEC:
            return list(self._cache_items)
        return self._refresh_locked()

    def get_payload(self) -> dict[str, Any]:
        raw = self.get_items()
        items_out: list[dict[str, Any]] = []
        for it in raw:
            d = dict(it)
            d["id"] = item_fingerprint(d)
            items_out.append(d)
        return {
            "status": "success",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "count": len(items_out),
            "items": items_out,
            "cwa_configured": bool((os.environ.get("CWA_AUTHORIZATION") or "").strip()),
            "rss_urls_configured": len(_env_feed_urls()),
        }

    def get_summary_for_chat(self, max_items: int = 8, max_chars: int = 900) -> str:
        items = self.get_items()[:max_items]
        if not items:
            return (
                "即時災害公開資訊：目前未取得資料（若需地震報告請在環境帳密設定 CWA_AUTHORIZATION；"
                "另可設定 DISASTER_FEED_URLS 以訂閱 RSS／Atom）。"
            )
        lines = ["即時災害／警訊摘要（來自公開資料與訂閱，非官方即時推播）："]
        for i, it in enumerate(items, 1):
            src = it.get("source", "")
            title = it.get("title", "")
            pub = it.get("published", "")
            link = it.get("link", "")
            extra = it.get("detail", "")
            row = f"{i}. [{src}] {title}"
            if pub:
                row += f"（{pub}）"
            if extra:
                row += f" — {extra}"
            if link:
                row += f" {link}"
            lines.append(row)
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 20] + "\n…（以下略）"
        return text


def item_fingerprint(details: dict[str, Any]) -> str:
    base = f"{details.get('source', '')}|{details.get('title', '')}|{details.get('published', '')}"
    return hashlib.sha256(base.encode("utf-8", errors="replace")).hexdigest()[:20]
