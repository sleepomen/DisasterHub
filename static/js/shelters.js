// 避難所標記：載入、收容率配色、彈出視窗、地區篩選；海岸線（陸地判定）
// 所有檔案用一般的 <script> 依序載入、共用同一個全域範圍（見 index.html 的順序）；
// 頂層只呼叫同一個檔案或前面檔案定義的函式，其餘都在事件發生時才呼叫

let markers = [];
let disasterCircle = null;
let allShelterData = [];

// 人口模型（海岸線 + 鄉鎮人口）由後端提供，與 AI 助手引用的是同一份資料
let popModel = { coastline: [], townships: [] };

async function loadPopulationModel() {
    try {
        const res = await fetch('/api/population');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        popModel = await res.json();
    } catch (err) {
        console.error('載入人口模型失敗，人群動畫將改用均勻分佈', err);
    }
}

function coastLonAt(lat) {
    const c = popModel.coastline;
    if (!c.length) return null;
    if (lat >= c[0][0]) return c[0][1];
    if (lat <= c[c.length - 1][0]) return c[c.length - 1][1];
    for (let i = 0; i < c.length - 1; i++) {
        const [la, loA] = c[i], [lb, loB] = c[i + 1];
        if (lat <= la && lat >= lb) {
            const t = (la - lat) / (la - lb);
            return loA + (loB - loA) * t;
        }
    }
    return c[c.length - 1][1];
}

function isLand(lat, lon) {
    const coast = coastLonAt(lat);
    return coast === null || lon <= coast - 0.004;
}

// 每個避難所的目前人數（可動態變化）
const shelterOccupancy = {};

function getOccupancyColor(rate) {
    if (rate < 0.5) return '#2ee6a8';
    if (rate < 0.8) return '#ff7a3d';
    return '#ff3d5c';
}

function createMarkerIcon(baseColor, size, occupancyRate) {
    const color = occupancyRate > 0 ? getOccupancyColor(occupancyRate) : baseColor;
    const s = size + 12;
    return L.divIcon({
        className: '',
        html: `<svg width="${s}" height="${s}" viewBox="0 0 28 28" style="filter:drop-shadow(0 2px 5px rgba(6,10,20,0.65));overflow:visible">
            <path d="M14 2 L24.4 8 V20 L14 26 L3.6 20 V8 Z" fill="#10141f" fill-opacity="0.55" stroke="${color}" stroke-width="1.8"/>
            <path d="M14 7.4 L19.7 10.7 V17.3 L14 20.6 L8.3 17.3 V10.7 Z" fill="${color}"/>
            <line x1="14" y1="2" x2="14" y2="5.2" stroke="${color}" stroke-width="1.6"/>
            <line x1="3.6" y1="20" x2="6.4" y2="18.4" stroke="${color}" stroke-width="1.6"/>
            <line x1="24.4" y1="20" x2="21.6" y2="18.4" stroke="${color}" stroke-width="1.6"/>
            <circle cx="14" cy="14" r="1.6" fill="#10141f"/>
        </svg>`,
        iconSize: [s, s],
        iconAnchor: [s/2, s/2],
    });
}

function showLoadError(message) {
    const banner = document.getElementById('app-error');
    const text = document.getElementById('app-error-text');
    if (!banner || !text) return;
    text.textContent = message;
    banner.hidden = false;
}

function clearLoadError() {
    const banner = document.getElementById('app-error');
    if (banner) banner.hidden = true;
}

async function loadShelters() {
    try {
        const res = await fetch('/api/shelters');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        allShelterData = await res.json();
    } catch (err) {
        // 後端或資料庫掛掉時不要只留一張空白地圖，要講清楚發生什麼事
        console.error('載入避難所資料失敗', err);
        showLoadError(I18N.t('load_error'));
        addChat(I18N.t('load_error_chat'), 'ai');
        return;
    }
    clearLoadError();
    allShelterData.forEach(d => { shelterOccupancy[d.name] = d.ppl || 0; });
    const statShelters = document.getElementById('stat-shelters');
    const statCapacity = document.getElementById('stat-capacity');
    if (statShelters) statShelters.textContent = allShelterData.length;
    if (statCapacity) statCapacity.textContent = allShelterData.reduce((s, d) => s + (d.z || 0), 0).toLocaleString();
    renderMarkers(allShelterData);
}

function renderMarkers(data) {
    markers.forEach(m => map.removeLayer(m));
    markers = [];

    data.forEach(d => {
        const baseColor = d.name.includes('YILAN') ? '#4da6ff' : '#2ee6a8';
        // 消防署資料的容量中位數只有 70 人：用平方根才分得出大小，線性的話幾乎全部縮成最小值
        const size = Math.max(10, Math.min(26, 6 + Math.sqrt(d.z || 0)));
        const occ = shelterOccupancy[d.name] || 0;
        const rate = d.z > 0 ? occ / d.z : 0;

        const marker = L.marker([d.lat, d.lon], {
            icon: createMarkerIcon(baseColor, size, rate)
        });

        marker.bindPopup(() => buildPopup(d));
        marker.userData = d;
        marker.baseColor = baseColor;
        marker.markerSize = size;
        marker.addTo(map);
        markers.push(marker);
    });
}

function escapeHtml(text) {
    return String(text ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}

function buildPopup(d) {
    const name = `<div class="popup-name">${escapeHtml(d.name.replace(/\[.*?\]\s*/,''))}</div>`;
    // 消防署資料：沒填的欄位不顯示，不寫「否」
    const details = [];
    const sep = I18N.lang === 'en' ? ', ' : '、';
    if (d.hazards && d.hazards.length) {
        details.push([I18N.t('popup_hazards'), d.hazards.map(h => I18N.t(`hazard_${h}`)).join(sep)]);
    }
    const place = [d.indoor ? I18N.t('venue_indoor') : '', d.outdoor ? I18N.t('venue_outdoor') : ''].filter(Boolean).join(sep);
    if (place) details.push([I18N.t('popup_venue'), place]);
    if (d.vulnerable_ok === true) details.push([I18N.t('popup_vulnerable'), I18N.t('vulnerable_ok')]);
    if (d.phone) details.push([I18N.t('popup_phone'), d.phone]);
    if (d.address) details.push([I18N.t('popup_address'), d.address]);
    const detailRows = details
        .map(([k, v]) => `<div class="popup-row"><span>${k}</span><span>${escapeHtml(v)}</span></div>`)
        .join('');
    if (!(d.z > 0)) {
        return name + `<div class="popup-row"><span>${I18N.t('popup_capacity')}</span><span>${I18N.t('not_provided')}</span></div>` + detailRows;
    }
    const occ = shelterOccupancy[d.name] || 0;
    const remaining = Math.max(0, d.z - occ);
    const rate = Math.min(100, Math.round(occ / d.z * 100));
    const barColor = rate < 50 ? '#2ee6a8' : rate < 80 ? '#ff7a3d' : '#ff3d5c';
    return name + `
        <div class="popup-row"><span>${I18N.t('popup_capacity')}</span><span>${d.z} ${I18N.t('people_unit')}</span></div>
        <div class="popup-row"><span>${I18N.t('popup_current')}</span><span>${occ} ${I18N.t('people_unit')}</span></div>
        <div class="popup-row"><span>${I18N.t('popup_remaining')}</span><span>${remaining} ${I18N.t('people_unit')}</span></div>
        <div class="popup-row"><span>${I18N.t('popup_load')}</span><span style="color:${barColor};font-weight:600">${rate}%</span></div>
        <div class="popup-bar"><div class="popup-bar-fill" style="width:${rate}%;background:${barColor}"></div></div>
    ` + detailRows;
}

function updateMarkerColor(markerObj) {
    const d = markerObj.userData;
    const occ = shelterOccupancy[d.name] || 0;
    const rate = d.z > 0 ? occ / d.z : 0;
    markerObj.setIcon(createMarkerIcon(markerObj.baseColor, markerObj.markerSize, rate));
}

function filterMarkers() {
    const filter = state.city;
    const filtered = filter === 'all' ? allShelterData : allShelterData.filter(d => d.name.includes(filter));
    renderMarkers(filtered);
    if (filter !== 'all' && filtered.length > 0) {
        const avg_lat = filtered.reduce((s, d) => s + d.lat, 0) / filtered.length;
        const avg_lon = filtered.reduce((s, d) => s + d.lon, 0) / filtered.length;
        map.flyTo([avg_lat, avg_lon], 11, { duration: 1 });
    } else {
        map.flyTo([23.9, 121.6], 9, { duration: 1 });
    }
}
