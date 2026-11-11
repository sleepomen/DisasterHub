"""
錄一支展示影片：現場網路或 Ollama 出狀況時，直接播這支接著講。

一鏡到底操作正在跑的網站，畫面下方加字幕說明每一步：選中心、執行模擬、沿道路疏散、路線提示、
追問分配結果、清單與小地名的問答、（有測試警戒時）紅色警戒切斷村里，最後清除模擬。
錄出來是 WebM（Playwright 的格式），檔名是 demo-en.webm 或 demo-zh.webm。簡報軟體比較吃 MP4，
可以用 ffmpeg 轉（指令在 docs/setup.md 的展示前檢查）。

需要服務在跑、主機上有 Playwright，並用環境變數提供管理員帳號（模擬要登入）：
    pip install playwright && playwright install chromium ffmpeg   # 或 --channel msedge 用本機的 Edge（ffmpeg 仍要裝）
    ADMIN_USERNAME=... ADMIN_PASSWORD=... python tools/record_demo.py --out ../DisasterHub-demo
    python tools/record_demo.py --lang zh --alerts --out ../DisasterHub-demo

--alerts 會多錄一段紅色警戒：資料庫裡要先有警戒（真的警戒，或展示用插入的測試警戒），否則那一段看不出效果。
會寫入收容人數，結束時重置。
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

CAPTIONS = {
    "en": {
        "intro": ("East Taiwan Shelter Decision Support — 644 official shelters, 554 villages, "
                  "the road network of Yilan, Hualien and Taitung"),
        "pick": "Click anywhere on the map to pick the disaster centre",
        "run": "Every affected village is an origin; shelters are assigned so that total travel time is shortest",
        "move": "Evacuees follow the actual roads; shelters turn red as they fill",
        "route": "Each line is one village → shelter assignment, with headcount, distance and time on foot and by car",
        "followup": "Follow-up questions are answered by code from the same plan — no model, no invented numbers",
        "list": "Filtered lists come straight from the shelter data in under a second",
        "place": "Small place names are turned into coordinates and answered by road distance",
        "alerts": "A red debris-flow alert closes the roads in its zone — these villages are left with no safe road",
        "end": "Code: github.com/sleepomen/DisasterHub",
    },
    "zh": {
        "intro": "東台灣避難所決策系統：644 間官方避難所、554 個村里、宜花東三縣路網",
        "pick": "在地圖上任何地方點一下，就是模擬中心",
        "run": "每個受影響的村里都是起點；分配讓所有人的通行時間加總最短",
        "move": "人群沿著真實道路移動，避難所滿了就變紅",
        "route": "每條線是一筆「村里 → 避難所」的分配，附人數、距離、步行與開車時間",
        "followup": "追問由程式依同一份分配回答，不經過模型，數字不會編造",
        "list": "清單題直接從避難所資料排出來，不到一秒",
        "place": "小地名換成座標，依道路距離回答",
        "alerts": "土石流紅色警戒封閉範圍內的道路，這幾個村里沒有安全道路可走",
        "end": "程式碼：github.com/sleepomen/DisasterHub",
    },
}
QUESTIONS = {"followup": "這次模擬每間避難所分配了多少人？", "list": "花蓮市的國小", "place": "七星潭附近有避難所嗎"}

# 地圖物件不是全域變數：在 Leaflet 建立地圖時記下來，才能移動視角、找圖層（跟 e2e/test_browser.py 一樣）
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
CAPTION_JS = """
text => {
  let box = document.getElementById('demo-caption');
  if (!box) {
    box = document.createElement('div');
    box.id = 'demo-caption';
    box.style.cssText = 'position:fixed;left:50%;bottom:28px;transform:translateX(-50%);max-width:760px;z-index:5000;'
      + 'background:rgba(31,58,95,.94);color:#fff;font:600 17px/1.5 "Noto Sans TC",system-ui,sans-serif;'
      + 'padding:12px 20px;border-radius:8px;box-shadow:0 6px 24px rgba(0,0,0,.25);pointer-events:none;text-align:center';
    document.body.appendChild(box);
  }
  box.textContent = text;
  box.style.display = text ? 'block' : 'none';
}
"""


class Demo:
    def __init__(self, page, lang: str):
        self.page, self.text = page, CAPTIONS[lang]

    def caption(self, key: str | None, hold_ms: int = 0):
        self.page.evaluate(CAPTION_JS, self.text[key] if key else "")
        if hold_ms:
            self.page.wait_for_timeout(hold_ms)

    def click_map(self, lat: float, lon: float):
        """
        在地圖上點一下選中心。那個位置剛好有避難所標記或路線的話，點下去會打開它的資訊框而不是選中心
        （第一次錄影就點到明義國小），所以在附近一圈一圈往外找一個底下只有地圖的位置
        """
        point = self.page.evaluate("""([lat, lon]) => {
            const m = window.__map, p = m.latLngToContainerPoint([lat, lon]), r = m.getContainer().getBoundingClientRect();
            const free = (x, y) => {
                const el = document.elementFromPoint(x, y);
                return el && m.getContainer().contains(el) && !el.closest('.leaflet-marker-pane, .leaflet-popup-pane, .leaflet-control')
                    && !(el.classList && el.classList.contains('leaflet-interactive'));
            };
            for (let ring = 0; ring <= 8; ring++) {
                for (let k = 0; k < Math.max(1, ring * 8); k++) {
                    const a = 2 * Math.PI * k / Math.max(1, ring * 8);
                    const x = p.x + r.left + ring * 14 * Math.cos(a), y = p.y + r.top + ring * 14 * Math.sin(a);
                    if (free(x, y)) return {x, y};
                }
            }
            return {x: p.x + r.left, y: p.y + r.top};
        }""", [lat, lon])
        self.page.mouse.move(point["x"] - 40, point["y"] - 30)
        self.page.mouse.move(point["x"], point["y"], steps=12)
        self.page.mouse.click(point["x"], point["y"])
        self.page.evaluate("() => window.__map.closePopup()")

    def choose(self, key: str, label: str):
        self.page.click(f"#{key}-trigger")
        self.page.wait_for_timeout(500)
        self.page.click(f"#{key}-dropdown [data-label='{label}']")
        self.page.wait_for_timeout(500)

    def ask(self, question: str):
        before = self.page.locator("#chat-container > *").count()
        self.page.click("#user-input")
        self.page.keyboard.type(question, delay=90)
        self.page.wait_for_timeout(400)
        self.page.keyboard.press("Enter")
        self.page.wait_for_function(
            f"!document.querySelector('#chat-container .msg-loading')"
            f" && document.querySelectorAll('#chat-container > *').length >= {before + 2}", timeout=180000)

    def simulate(self):
        self.page.click("#btn-simulate")
        self.page.wait_for_function("document.getElementById('btn-simulate').disabled", timeout=60000)

    def wait_simulation(self):
        self.page.wait_for_function("!document.getElementById('btn-simulate').disabled", timeout=300000)

    def view(self, lat: float, lon: float, zoom: int):
        self.page.evaluate("([lat, lon, z]) => window.__map.flyTo([lat, lon], z, {duration: 1.5})", [lat, lon, zoom])
        self.page.wait_for_timeout(2000)


def record(args) -> Path:
    user, password = os.environ.get("ADMIN_USERNAME"), os.environ.get("ADMIN_PASSWORD")
    if not user or not password:
        sys.exit("請用環境變數 ADMIN_USERNAME / ADMIN_PASSWORD 提供管理員帳號（模擬要登入）")
    out = Path(args.out)
    tmp = out / "_recording"
    tmp.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, record_video_dir=str(tmp),
                                      record_video_size={"width": 1440, "height": 900})
        # 先在同一個 context 登入，網頁一打開就是登入狀態，不會錄到登入視窗
        context.request.post(f"{args.site}/api/login", data={"username": user, "password": password}).ok or sys.exit("登入失敗")
        context.request.post(f"{args.site}/api/reset_simulation")
        context.add_init_script(MAP_HOOK)
        context.add_init_script(f"try {{ localStorage.setItem('disasterhub.lang', '{'en' if args.lang == 'en' else 'zh'}'); }} catch {{}}")
        page = context.new_page()
        page.goto(args.site, wait_until="networkidle")
        page.wait_for_timeout(1500)
        demo = Demo(page, args.lang)

        demo.caption("intro", 4500)
        demo.view(23.98, 121.58, 12)
        demo.caption("pick")
        demo.click_map(23.982, 121.603)
        page.wait_for_timeout(2200)
        demo.choose("mode", "mode_drive")
        demo.caption("run")
        demo.simulate()
        page.wait_for_timeout(1300)
        page.evaluate("() => { window.__map.stop(); window.__map.setView([23.985, 121.60], 13, {animate: true}); }")
        page.wait_for_timeout(2500)
        demo.caption("move")
        demo.wait_simulation()
        page.wait_for_timeout(1500)

        demo.caption("route")
        page.evaluate("""() => {
            const map = window.__map; let best = null;
            map.eachLayer(l => {
                if (l instanceof L.Polyline && !(l instanceof L.Polygon) && l.options.color === THEME.route) {
                    const pts = l.getLatLngs(); if (pts.length < 2) return;
                    const mid = pts[Math.floor(pts.length / 2)], c = map.latLngToContainerPoint(mid), size = map.getSize();
                    const inside = c.x > 330 && c.x < size.x - 380 && c.y > 120 && c.y < size.y - 160;
                    if (inside && (!best || l.options.weight > best.w)) best = {w: l.options.weight, l, mid};
                }
            });
            if (best) best.l.openTooltip(best.mid);
        }""")
        page.wait_for_timeout(4500)
        page.evaluate("() => window.__map.closeTooltip && window.__map.eachLayer(l => l.closeTooltip && l.closeTooltip())")

        demo.caption("followup")
        demo.ask(QUESTIONS["followup"])
        page.wait_for_timeout(4500)
        demo.caption("list")
        demo.ask(QUESTIONS["list"])
        page.wait_for_timeout(4000)
        demo.caption("place")
        demo.ask(QUESTIONS["place"])
        page.wait_for_timeout(4500)

        if args.alerts:
            context.request.post(f"{args.site}/api/reset_simulation")
            page.wait_for_timeout(1500)
            demo.caption(None)
            demo.choose("loc", "loc_taitung")
            demo.choose("type", "type_flood")
            demo.choose("mode", "mode_walk")
            page.evaluate("() => { const s = document.getElementById('sim-radius'); s.value = 20;"
                          " s.dispatchEvent(new Event('input', {bubbles: true})); }")
            page.wait_for_timeout(800)
            demo.simulate()
            demo.wait_simulation()
            page.evaluate("() => window.__map.flyTo([22.885, 121.085], 13, {duration: 1.5})")
            page.wait_for_timeout(2200)
            demo.caption("alerts")
            page.evaluate("""() => window.__map.eachLayer(l => {
                if (l.feature && l.feature.properties && l.feature.properties.debris_no === '東縣DF034') {
                    l.getPopup().options.autoPan = false; l.openPopup();
                }
            })""")
            page.wait_for_timeout(6000)

        demo.caption("end", 3500)
        context.request.post(f"{args.site}/api/reset_simulation")
        video = page.video.path()
        context.close()
        browser.close()
    target = out / f"demo-{args.lang}.webm"
    shutil.move(video, target)
    shutil.rmtree(tmp, ignore_errors=True)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site", default="http://localhost:8501")
    parser.add_argument("--lang", choices=("en", "zh"), default="en")
    parser.add_argument("--alerts", action="store_true", help="多錄一段紅色警戒（資料庫裡要先有警戒）")
    parser.add_argument("--channel", default=os.environ.get("E2E_BROWSER_CHANNEL") or None,
                        help="用本機已安裝的瀏覽器，例如 msedge、chrome")
    parser.add_argument("--out", default="demo")
    args = parser.parse_args()
    print(f"錄好了：{record(args)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
