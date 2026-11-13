"""「現在有土石流警戒嗎」由程式讀即時警戒回答"""
from datetime import datetime, timedelta, timezone

import pytest

from services.chat.alert_answer import alert_answer, asks_current_alerts

TW = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 13, 17, 20, tzinfo=TW)


def alert(level, county, town, village, ref_no, road_m=0):
    return {"ref_no": ref_no, "level": level, "county": county, "town": town, "village": village,
            "fetched_at": NOW, "road_m": road_m}


ALERTS = [
    alert("red", "花蓮縣", "秀林鄉", "富世村", "花縣DF045", road_m=1234),
    alert("yellow", "臺東縣", "海端鄉", "利稻村", "東縣DF034"),
]


@pytest.mark.parametrize("query", ["現在有土石流警戒嗎", "花蓮目前有土石流警戒嗎", "有哪些地方發布紅色警戒"])
def test_questions_about_alerts_now_are_recognised(query):
    assert asks_current_alerts(query)


@pytest.mark.parametrize("query", ["黃色警戒是什麼意思", "紅色警戒有沒有要撤離", "土石流紅色警戒要怎麼辦",
                                   "現在有警戒，可以去哪個避難所"])
def test_what_an_alert_means_and_shelter_questions_are_not(query):
    assert not asks_current_alerts(query)


def test_alerts_in_effect_are_listed_with_their_roads_and_source():
    text = alert_answer("現在有土石流警戒嗎", ALERTS, NOW)
    assert text.startswith("目前生效的土石流警戒共 2 條（紅色 1、黃色 1）")
    assert "- 紅色｜花蓮縣秀林鄉富世村（花縣DF045），範圍內道路 1.2 公里封閉" in text
    assert "- 黃色｜臺東縣海端鄉利稻村（東縣DF034）" in text
    assert "11-13 17:20 更新" in text


@pytest.mark.parametrize("query,expected", [("台東目前有土石流警戒嗎", "東縣DF034"), ("秀林鄉現在有警戒嗎", "花縣DF045")])
def test_a_named_county_or_township_narrows_the_list(query, expected):
    text = alert_answer(query, ALERTS, NOW)
    assert "共 1 條" in text and expected in text


def test_an_area_without_alerts_says_so():
    assert alert_answer("宜蘭現在有土石流警戒嗎", ALERTS, NOW).startswith("宜蘭目前沒有生效的土石流警戒")


def test_no_alerts_is_only_claimed_when_the_data_was_just_updated():
    assert alert_answer("現在有土石流警戒嗎", [], NOW).startswith("目前沒有生效的土石流警戒")
    # 更新一直失敗時舊警戒會過期，資料庫看起來也是空的：不能說「沒有警戒」
    assert alert_answer("現在有土石流警戒嗎", [], None).startswith("目前無法確認土石流警戒狀態")
    assert alert_answer("現在有土石流警戒嗎", None, NOW).startswith("目前無法確認土石流警戒狀態")


def test_alert_answer_in_english():
    text = alert_answer("現在有土石流警戒嗎", ALERTS, NOW, lang="en")
    assert text.startswith("Debris-flow alerts in effect: 2 (1 red, 1 yellow).")
    assert "- Red: 花蓮縣秀林鄉富世村 (花縣DF045), 1.2 km of road closed" in text


def test_the_refresh_time_expires(monkeypatch):
    from services.geo import hazard_alerts
    monkeypatch.setattr(hazard_alerts, "_last_refresh", (0.0, NOW))
    monkeypatch.setattr(hazard_alerts.time, "monotonic", lambda: 60.0)
    assert hazard_alerts.last_refresh() == NOW
    monkeypatch.setattr(hazard_alerts.time, "monotonic", lambda: 10 ** 9)
    assert hazard_alerts.last_refresh() is None
