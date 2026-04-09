"""
自遠端 CSV（例如政府開放資料避難所清單）載入避難所，與本地 JSON 合併。
透過欄位別名自動對應常見中文表頭；可用 OPENDATA_SHELTER_COUNTY_FILTER 限定縣市（逗號分隔）。

【專案改動】用以漸進取代／補強僅依 data_for_refuge 之 mock：經 OPENDATA_SHELTER_CSV_URL
由 DataFetcher 在同步時併入；MCP 之 preview_remote_shelter_csv 可預覽筆數而不寫庫。
"""
from __future__ import annotations

import io
import os
import re
import pandas as pd
import requests

from models.shelter import Shelter

REQUEST_TIMEOUT = 45

NAME_KEYS = ("避難收容處所名稱", "避難所名稱", "名稱", "收容所名稱", "name", "NAME", "shelter_name")
LAT_KEYS = ("緯度", "lat", "Lat", "LAT", "latitude", "Latitude", "緯度(WGS84)", "北緯")
LON_KEYS = ("經度", "lon", "Lon", "LNG", "lng", "longitude", "Longitude", "經度(WGS84)", "東經")
CAP_KEYS = ("預計收容人數", "容納人數", "收容人數", "容量", "capacity", "CAPACITY", "可容納人數")
COUNTY_KEYS = ("縣市", "縣市及鄉鎮市區", "行政區", "city", "County")


def _pick_column(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    col_set = {c.strip(): c for c in columns}
    for key in candidates:
        if key in col_set:
            return col_set[key]
    for c in columns:
        s = c.strip()
        for key in candidates:
            if key.lower() in s.lower():
                return c
    return None


def _parse_county_filter() -> list[str] | None:
    raw = os.environ.get("OPENDATA_SHELTER_COUNTY_FILTER", "").strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or None


def load_shelters_from_csv_url(url: str) -> list[Shelter]:
    if not url.strip():
        return []

    try:
        r = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "DisasterHub/1.0 (shelter CSV)"},
        )
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"[shelter_remote_fetch] 下載失敗：{e}")
        return []

    for encoding in ("utf-8-sig", "utf-8", "big5", "cp950"):
        try:
            df = pd.read_csv(io.BytesIO(r.content), encoding=encoding)
            break
        except Exception:
            df = None
    if df is None or df.empty:
        print("[shelter_remote_fetch] CSV 解析失敗或無資料")
        return []

    cols = [str(c) for c in df.columns]
    col_name = _pick_column(cols, NAME_KEYS)
    col_lat = _pick_column(cols, LAT_KEYS)
    col_lon = _pick_column(cols, LON_KEYS)
    col_cap = _pick_column(cols, CAP_KEYS)
    col_county = _pick_column(cols, COUNTY_KEYS)

    if not col_name or not col_lat or not col_lon:
        print(f"[shelter_remote_fetch] 表頭無法對應 name/lat/lon，欄位：{cols[:20]}")
        return []

    county_filter = _parse_county_filter()
    out: list[Shelter] = []

    for _, row in df.iterrows():
        try:
            county_val = ""
            if col_county and pd.notna(row.get(col_county)):
                county_val = str(row[col_county]).strip()

            if county_filter and county_val:
                if not any(c in county_val for c in county_filter):
                    continue

            name = str(row[col_name]).strip()
            if not name or name.lower() == "nan":
                continue

            lat = float(row[col_lat])
            lon = float(row[col_lon])
            if not (20 <= lat <= 27 and 118 <= lon <= 124):
                continue

            cap = 0
            if col_cap and pd.notna(row.get(col_cap)):
                cap = int(float(re.sub(r"[^\d.]", "", str(row[col_cap])) or 0))

            prefix = "[OPENDATA]"
            if county_val:
                prefix = f"[OPENDATA:{county_val[:12]}]"

            out.append(
                Shelter(
                    name=f"{prefix} {name}",
                    total_vessel=max(cap, 0),
                    total_people=0,
                    lat=lat,
                    lon=lon,
                )
            )
        except Exception:
            continue

    print(f"[shelter_remote_fetch] 自遠端載入 {len(out)} 筆避難所")
    return out


def merge_shelter_lists(primary: list[Shelter], secondary: list[Shelter]) -> list[Shelter]:
    """primary 優先；同名（完全相符）則保留 primary。"""
    seen = {s.name for s in primary}
    merged = list(primary)
    for s in secondary:
        if s.name in seen:
            continue
        seen.add(s.name)
        merged.append(s)
    return merged


def remote_shelter_url_from_env() -> str:
    return os.environ.get("OPENDATA_SHELTER_CSV_URL", "").strip()
