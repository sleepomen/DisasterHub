"""
瀏覽器冒煙測試：用真的瀏覽器操作正在跑的網站，每個前端功能走一次，收集頁面錯誤。

tests/ 底下的測試都用假的資料庫與模型，前端（static/js/*.js）沒有被測到；
2026-11-09 把 app.js 拆成九個檔案時，就是靠這套步驟確認行為沒變。
放在 tests/ 外面：CI 與映像裡沒有瀏覽器，tests/ 的 conftest 也需要 psycopg2，主機上不一定有。

需要網站已經在跑（docker compose up），以及 Playwright：
    pip install pytest playwright
    playwright install chromium          # 或用本機的 Edge / Chrome：E2E_BROWSER_CHANNEL=msedge
    E2E_BASE_URL=http://localhost:8501 pytest e2e/ -v

test_simulation_round_trip 會登入、執行模擬並寫入收容人數，結束時重置；
需要 ADMIN_USERNAME / ADMIN_PASSWORD（跟模擬追問評測一樣從環境變數讀），沒有就跳過。
"""
import os

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

BASE_URL = os.environ.get("E2E_BASE_URL", "")
CHANNEL = os.environ.get("E2E_BROWSER_CHANNEL") or None
if not BASE_URL:
    pytest.skip("沒有設定 E2E_BASE_URL，跳過瀏覽器測試", allow_module_level=True)

# 地圖物件不是全域變數：在 Leaflet 建立地圖的那一刻記下來，測試才能移動視角、換算座標、找圖層
MAP_HOOK = """
(() => {
  let real;
  Object.defineProperty(window, 'L', {
    configurable: true,
    get() { return real; },
    set(v) {
      const make = v.map;
      v.map = function (...args) { const m = make.apply(this, args); window.__map = m; return m; };
      real = v;
    },
  });
})();
"""
# 頭城鎮福成里附近的空地：沒有避難所標記、潛勢範圍蓋著，點下去一定是選中心
PICK = (24.89, 121.80)


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch(channel=CHANNEL)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(MAP_HOOK)
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("response", lambda r: r.status >= 400 and page.errors.append(f"{r.status} {r.url}"))
    page.goto(BASE_URL, wait_until="networkidle")
    yield page
    context.close()


def skip_login(page):
    try:
        page.wait_for_selector("#login-overlay", state="visible", timeout=8000)
        page.click("#btn-login-skip")
    except sync_api.TimeoutError:
        pass
    page.wait_for_selector("#login-overlay", state="hidden")
    page.wait_for_timeout(1000)


def click_map(page, lat, lon):
    point = page.evaluate("""([lat, lon]) => {
        const m = window.__map, p = m.latLngToContainerPoint([lat, lon]), r = m.getContainer().getBoundingClientRect();
        return {x: p.x + r.left, y: p.y + r.top};
    }""", [lat, lon])
    page.mouse.click(point["x"], point["y"])
    page.wait_for_timeout(1500)


def count_layers(page, color):
    return page.evaluate("""color => { let n = 0; window.__map.eachLayer(l => {
        if (l instanceof L.Path && !(l instanceof L.Polygon) && l.options.color === color) n++; }); return n; }""", color)


def last_message(page):
    return page.locator("#chat-container > *").last.inner_text()


def ask(page, question):
    before = page.locator("#chat-container > *").count()
    page.fill("#user-input", question)
    page.press("#user-input", "Enter")
    page.wait_for_function(
        f"!document.querySelector('#chat-container .msg-loading')"
        f" && document.querySelectorAll('#chat-container > *').length >= {before + 2}", timeout=120000)
    return last_message(page)


def test_browse_without_login(page):
    skip_login(page)
    total = page.locator(".leaflet-marker-icon").count()
    assert total > 600

    # 地區篩選
    page.click("#city-display")
    page.click("#city-dropdown [data-i18n='city_yilan']")
    page.wait_for_timeout(800)
    assert 0 < page.locator(".leaflet-marker-icon").count() < total

    # 避難所資訊框
    page.locator(".leaflet-marker-icon").first.click(force=True)
    page.wait_for_timeout(600)
    assert "容量" in page.locator(".leaflet-popup-content").inner_text()
    page.evaluate("() => window.__map.closePopup()")

    # 在地圖上點選模擬中心：名稱由 /api/locate 標上
    page.evaluate("() => window.__map.setView([24.85, 121.80], 11, {animate: false})")
    page.wait_for_timeout(1000)
    click_map(page, *PICK)
    assert page.locator("#loc-display").inner_text().startswith("頭城鎮")
    assert count_layers(page, "#ffbe2e") == 1

    # 語言切換：介面與選點名稱一起換，再換回來
    page.click("#btn-lang")
    page.wait_for_timeout(600)
    assert "Toucheng Township" in page.locator("#loc-display").inner_text()
    assert page.locator("#btn-simulate").inner_text() == "Run simulation"
    page.click("#btn-lang")
    page.wait_for_timeout(600)

    # 選回預設中心，選點標記要消失
    page.click("#loc-display")
    page.click("#loc-dropdown [data-i18n='loc_hualien']")
    assert count_layers(page, "#ffbe2e") == 0

    # 聊天：列表題由程式直接回答，不需要模型
    assert "花蓮市" in ask(page, "花蓮市的國小")
    assert page.errors == []


def test_simulation_round_trip(page):
    user, password = os.environ.get("ADMIN_USERNAME"), os.environ.get("ADMIN_PASSWORD")
    if not user or not password:
        pytest.skip("沒有 ADMIN_USERNAME / ADMIN_PASSWORD，跳過需要登入的模擬")
    if not page.locator("#login-overlay").is_visible():
        page.click("#btn-login-open")
    page.fill("#login-user", user)
    page.fill("#login-pass", password)
    page.click("#btn-login")
    page.wait_for_selector("#login-overlay", state="hidden")
    page.wait_for_timeout(1000)
    try:
        page.evaluate("() => window.__map.setView([24.85, 121.80], 11, {animate: false})")
        page.wait_for_timeout(1000)
        click_map(page, *PICK)
        page.evaluate("() => { const s = document.getElementById('sim-radius'); s.value = 8;"
                      " s.dispatchEvent(new Event('input', {bubbles: true})); }")
        assert page.locator("#radius-val").inner_text() == "8 km"

        page.click("#btn-simulate")
        page.wait_for_function("document.getElementById('btn-simulate').disabled", timeout=30000)
        page.wait_for_function("!document.getElementById('btn-simulate').disabled", timeout=240000)
        page.wait_for_timeout(1000)
        assert "疏散模擬完成" in last_message(page)
        assert count_layers(page, "#ffe14d") + count_layers(page, "#ff9f43") > 0  # 路線
        assert count_layers(page, "#e8f0ff") > 0  # 村里起點

        before = page.locator("#chat-container > *").count()
        page.click("#btn-reset")
        page.wait_for_function(f"document.querySelectorAll('#chat-container > *').length > {before}", timeout=120000)
        assert "已還原" in last_message(page)
        assert count_layers(page, "#e8f0ff") == 0
    finally:
        # 不論成敗都把收容人數還原，不留下測試的模擬
        page.evaluate("fetch('/api/reset_simulation', {method: 'POST'})")
        page.wait_for_timeout(2000)
    assert page.errors == []
