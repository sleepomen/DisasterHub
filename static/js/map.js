// Leaflet 地圖、底圖，以及土石流潛勢範圍與即時警戒的圖層
// 所有檔案用一般的 <script> 依序載入、共用同一個全域範圍（見 index.html 的順序）；
// 頂層只呼叫同一個檔案或前面檔案定義的函式，其餘都在事件發生時才呼叫

// ── Leaflet ──────────────────────────────────────────────
const map = L.map('map', { center: [23.9, 121.6], zoom: 9, zoomControl: true });

// 底圖用 OpenStreetMap 官方圖磚，深色是 style.css 對 .leaflet-tile-pane 套濾鏡轉出來的。
// 原本用 CARTO dark_all，但 CARTO 改成要 API key，沒帶 key 的請求一律回
// 「API KEY REQUIRED」浮水印圖磚，整張地圖會被浮水印鋪滿。OSM 不用帳號，
// 但使用政策要求保留下面的 attribution
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
}).addTo(map);

// 土石流潛勢溪流影響範圍（農業部開放資料），疊在底圖上、標記下面。
// 淹水模擬會避開落在範圍內的避難所，畫出來使用者才看得懂為什麼某間沒有人去
// 即時土石流警戒（後端定期向農村水保署抓取）：發布警戒的範圍改成紅色或黃色，
// 紅色警戒範圍內的道路封閉、黃色警戒範圍內的道路盡量繞行，路線規劃用的是同一份資料
const ZONE_STYLE = { color: '#ff9f43', weight: 1, opacity: 0.7, fillColor: '#ff9f43', fillOpacity: 0.18 };
const ALERT_STYLE = {
    red: { color: '#ff3d5c', weight: 2, opacity: 0.9, fillColor: '#ff3d5c', fillOpacity: 0.35 },
    yellow: { color: '#ffd23d', weight: 2, opacity: 0.9, fillColor: '#ffd23d', fillOpacity: 0.3 },
};
let hazardLayer = null;
let alertsByRef = {};

function styleZone(f) {
    const alert = alertsByRef[(f.properties || {}).debris_no];
    return alert ? ALERT_STYLE[alert.level] : ZONE_STYLE;
}

async function loadHazardZones() {
    try {
        const res = await fetch('/api/hazard_zones');
        if (!res.ok) return;
        const data = await res.json();
        hazardLayer = L.geoJSON(data, {
            style: styleZone,
            onEachFeature: (f, layer) => {
                const p = f.properties || {};
                layer.bindPopup(() => {
                    const alert = alertsByRef[p.debris_no];
                    return `<div class="popup-name">${I18N.t('debris_stream')} ${p.debris_no || ''}</div>`
                        + (alert ? `<div class="popup-row"><span>${I18N.t('popup_alert')}</span><span>${I18N.t(`alert_${alert.level}`)}</span></div>` : '')
                        + `<div class="popup-row"><span>${I18N.t('popup_location')}</span><span>${p.county || ''}${p.town || ''}${p.village || ''}</span></div>`
                        + `<div class="popup-row"><span>${I18N.t('popup_risk')}</span><span>${p.risk || '-'}</span></div>`
                        + `<div class="popup-row"><span>${I18N.t('popup_households')}</span><span>${p.households ?? '-'} ${I18N.t('households_unit')}</span></div>`;
                });
            },
        }).addTo(map);
        hazardLayer.setStyle(styleZone);
    } catch (err) {
        // 疊圖只是輔助資訊，載不到不影響其他功能
        console.error('災害潛勢區域載入失敗', err);
    }
}

async function loadAlerts() {
    try {
        const res = await fetch('/api/alerts');
        if (!res.ok) return;
        const data = await res.json();
        alertsByRef = {};
        (data.alerts || []).forEach(a => { alertsByRef[a.ref_no] = a; });
        if (hazardLayer) hazardLayer.setStyle(styleZone);
        if (data.poll_seconds > 0) setTimeout(loadAlerts, data.poll_seconds * 1000);
    } catch (err) {
        console.error('土石流警戒載入失敗', err);
    }
}
loadHazardZones().then(loadAlerts);
