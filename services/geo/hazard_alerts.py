"""
即時災害警戒：目前接的是農村發展及水土保持署的「土石流及大規模崩塌警戒資料」
（data.gov.tw/dataset/148402，政府資料開放授權 1.0）。

每一筆警戒帶著潛勢溪流編號（DebrisNo，例如「花縣DF045」），跟 hazard_zones 的 ref_no 是同一套編號，
所以不用做模糊的地名或空間比對：警戒 → 潛勢範圍 → 範圍內的道路邊（road_hazards）直接對上。
紅色警戒（r）範圍內的道路封閉、黃色警戒（y）範圍內的道路盡量繞開，見 repositories 的 road_closures 視圖。

來源每次回傳的是「目前所有生效的警戒」，沒有警戒時回傳一筆 LandslideName=「目前無警戒」、其餘欄位是「-」的佔位資料。
所以每次抓到就整批取代；抓取失敗時保留上一次的結果，ALERT_TTL_MINUTES 分鐘後自動失效，
不會因為來源掛掉就讓一個早已解除的警戒永遠擋著道路
"""
import asyncio
import logging

import requests

import config
from services.web.metrics import metrics

logger = logging.getLogger(__name__)

SOURCE = "swcb_debris_alert"
HAZARD_TYPE = "debris_flow"
LEVELS = {"r": "red", "y": "yellow"}

metrics.register("alerts.refreshed", "alerts.failed")


class AlertFormatError(ValueError):
    """來源回傳的不是預期的格式：寧可保留上一次的警戒，也不要當成「沒有警戒」把封閉的道路打開"""


def parse_debris_alerts(rows) -> list[dict]:
    """來源 JSON → [{ref_no, level, county, town, village, issued_at}]，只留土石流警戒（AlertType D）"""
    if not isinstance(rows, list):
        raise AlertFormatError(f"預期是 list，拿到 {type(rows).__name__}")
    alerts = []
    for row in rows:
        if not isinstance(row, dict) or "DebrisNo" not in row or "AlertLevel" not in row:
            raise AlertFormatError(f"缺少 DebrisNo / AlertLevel 欄位：{row!r}"[:200])
        if row.get("AlertType") != "D":
            continue  # 大規模崩塌警戒、「目前無警戒」的佔位資料
        ref_no = str(row["DebrisNo"]).strip().replace("臺", "台").replace("df", "DF")
        level = LEVELS.get(str(row["AlertLevel"]).strip().lower())
        if not ref_no or ref_no == "-" or level is None:
            continue
        issued = str(row.get("LastUpdateDate") or "").strip()
        alerts.append({
            "ref_no": ref_no,
            "level": level,
            "county": row.get("County"),
            "town": row.get("Town"),
            "village": row.get("Vill"),
            # 來源時間沒有時區，是台灣時間
            "issued_at": f"{issued}+08:00" if issued and issued != "-" else None,
        })
    return alerts


def fetch_debris_alerts(url: str | None = None, timeout: float | None = None) -> list[dict]:
    response = requests.get(url or config.DEBRIS_ALERT_URL, timeout=timeout or config.ALERT_FETCH_TIMEOUT)
    response.raise_for_status()
    return parse_debris_alerts(response.json())


def refresh_alerts(repo) -> int:
    """抓一次警戒並整批寫入，回傳生效的警戒數；失敗時丟例外，資料庫裡上一次的警戒原封不動"""
    alerts = fetch_debris_alerts()
    count = repo.replace_alerts(SOURCE, HAZARD_TYPE, alerts, config.ALERT_TTL_MINUTES)
    metrics.incr("alerts.refreshed")
    reds = sum(1 for a in alerts if a["level"] == "red")
    logger.info("土石流警戒更新：%d 條（紅色 %d、黃色 %d）", count, reds, count - reds)
    return count


async def poll_alerts(repo) -> None:
    """背景定期更新警戒。單次失敗只記錄，下一輪再試"""
    while True:
        try:
            await asyncio.to_thread(refresh_alerts, repo)
        except Exception as e:
            metrics.incr("alerts.failed")
            logger.warning("土石流警戒更新失敗，沿用上一次的資料（%d 分鐘後失效）：%s", config.ALERT_TTL_MINUTES, e)
        await asyncio.sleep(config.ALERT_POLL_SECONDS)
