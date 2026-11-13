"""
「現在有土石流警戒嗎」：由程式讀即時警戒（hazard_alerts）回答，不交給模型，也不拿警戒等級的定義充數。

警戒資料由 services/geo/hazard_alerts.py 定期向農村發展及水土保持署更新。資料庫裡沒有生效的警戒，
不一定代表真的沒有：更新一直失敗時，舊警戒過了 ALERT_TTL_MINUTES 就失效，看起來也是「沒有」。
所以只有最近確實更新成功過，才說「目前沒有」；否則說無法確認，請使用者看官方網站
"""
from datetime import timedelta, timezone

from services.chat import query_rules

SITE = "https://246.ardswc.gov.tw"
# 容器的時區是 UTC；顯示給使用者的更新時間一律用台灣時間
TAIWAN = timezone(timedelta(hours=8))
MAX_LISTED = 20

LEVEL_ZH = {"red": "紅色", "yellow": "黃色"}
LEVEL_EN = {"red": "Red", "yellow": "Yellow"}

# 問的是「現在」：有這些字，或「有沒有／有哪些／哪裡有……警戒」的說法。
# 「黃色警戒是什麼意思」「紅色警戒有沒有要撤離」沒有這些說法（有沒有在警戒後面），仍是知識題
NOW_WORDS = ("現在", "目前", "今天", "此刻", "最新", "生效", "發布了嗎", "發佈了嗎")
ASK_BEFORE_ALERT = ("有沒有", "有無", "有哪些", "哪些地方", "哪裡有", "哪邊有")


def asks_current_alerts(query: str) -> bool:
    if "警戒" not in query or any(word in query for word in query_rules.SHELTER_TOPIC_WORDS):
        return False
    if any(word in query for word in NOW_WORDS):
        return True
    head = query[:query.find("警戒")]
    return any(word in head for word in ASK_BEFORE_ALERT)


def _norm(text: str | None) -> str:
    return (text or "").replace("臺", "台")


def _in_area(alert: dict, township: str | None, region: str | None) -> bool:
    if township:
        return _norm(alert.get("town")) == _norm(township)
    if region:
        return _norm(alert.get("county")).startswith(_norm(region))
    return True


def alert_answer(query: str, alerts: list[dict] | None, refreshed_at, lang: str = "zh") -> str:
    """
    alerts：repo.active_alerts() 的結果；None 表示查不到（沒有資料庫或查詢失敗）。
    refreshed_at：最近一次成功更新的時間，太久沒更新是 None（沒有警戒時就無法確認）
    """
    plan = query_rules.analyze(query, ignore_off_topic=True)
    township, region = plan.township, (None if plan.township else plan.region)
    area = township or region or ""
    en = lang == "en"

    if alerts is None or (not alerts and refreshed_at is None):
        if en:
            return ("I cannot confirm the current debris-flow alerts: the alert data has not been updated successfully "
                    f"recently. Please check the Debris Flow Disaster Prevention website ({SITE}).")
        return f"目前無法確認土石流警戒狀態：警戒資料最近沒有更新成功。請以農村水保署土石流防災資訊網（{SITE}）為準。"

    picked = [a for a in alerts if _in_area(a, township, region)]
    fetched = refreshed_at or max((a["fetched_at"] for a in alerts), default=None)
    when = fetched.astimezone(TAIWAN).strftime("%m-%d %H:%M") if fetched else ""
    source = (f"Source: Agency of Rural Development and Soil and Water Conservation, updated {when} ({SITE})." if en
              else f"資料來源：農業部農村發展及水土保持署，{when} 更新（{SITE}）。")

    if not picked:
        if en:
            where = f" in {area}" if area else ""
            return f"There are no debris-flow alerts in effect{where} right now.\n{source}"
        return f"{area}目前沒有生效的土石流警戒。\n{source}"

    reds = sum(1 for a in picked if a["level"] == "red")
    lines = []
    for a in picked[:MAX_LISTED]:
        place = f"{a.get('county') or ''}{a.get('town') or ''}{a.get('village') or ''}"
        km = round((a.get("road_m") or 0) / 1000, 1)
        if en:
            road = (f", {km} km of road closed" if a["level"] == "red" else f", {km} km of road to avoid") if km else ""
            lines.append(f"- {LEVEL_EN[a['level']]}: {place} ({a['ref_no']}){road}")
        else:
            road = (f"，範圍內道路 {km} 公里封閉" if a["level"] == "red" else f"，範圍內道路 {km} 公里建議繞行") if km else ""
            lines.append(f"- {LEVEL_ZH[a['level']]}｜{place}（{a['ref_no']}）{road}")
    rest = len(picked) - MAX_LISTED
    if en:
        head = (f"Debris-flow alerts in effect{' in ' + area if area else ''}: {len(picked)} "
                f"({reds} red, {len(picked) - reds} yellow).")
        tail = [f"{rest} more are shown on the map."] if rest > 0 else []
        tail.append("Ask \"what does a red alert mean\" for what to do at each level.")
    else:
        head = f"{area}目前生效的土石流警戒共 {len(picked)} 條（紅色 {reds}、黃色 {len(picked) - reds}）："
        tail = [f"其餘 {rest} 條請看地圖。"] if rest > 0 else []
        tail.append("各等級該怎麼做，可以問「紅色警戒是什麼意思」。")
    return "\n".join([head, *lines, *tail, source])
