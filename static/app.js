    const state = { city: 'all', loc: '121.60,23.98', type: 'earthquake' };

    function toggleDropdown(e, id) {
        e.stopPropagation();
        document.querySelectorAll('.dropdown-menu').forEach(m => {
            if (m.id !== id) m.classList.remove('open');
        });
        document.getElementById(id).classList.toggle('open');
    }

    // labelKey 是 i18n.js 的翻譯鍵；顯示欄也記住這個鍵，切換語言時才會跟著換
    function selectOption(e, key, value, labelKey) {
        e.stopPropagation();
        state[key] = value;
        const display = document.getElementById(`${key}-display`);
        display.dataset.i18n = labelKey;
        display.textContent = I18N.t(labelKey);
        const menu = document.getElementById(`${key}-dropdown`);
        menu.querySelectorAll('.dropdown-option').forEach(opt => {
            const label = opt.querySelector('[data-i18n]');
            const isSelected = !!label && label.dataset.i18n === labelKey;
            opt.classList.toggle('selected', isSelected);
            let check = opt.querySelector('.checkmark');
            if (isSelected && !check) {
                check = document.createElement('span');
                check.className = 'checkmark';
                check.textContent = '✓';
                opt.appendChild(check);
            } else if (!isSelected && check) { check.remove(); }
        });
        menu.classList.remove('open');
        if (key === 'city') filterMarkers();
    }

    document.addEventListener('click', () => {
        document.querySelectorAll('.dropdown-menu').forEach(m => m.classList.remove('open'));
    });

    // ── 登入狀態 ─────────────────────────────────────────────
    // 模擬 / 收容人數回寫 / 重置會改後端資料庫與全域狀態，後端要求先登入。
    // session 放在 HttpOnly cookie，由瀏覽器自動帶，前端不會碰到憑證本身；
    // 開網站登入一次，之後整個班次不必再輸入任何東西。
    const authState = { user: null, loginEnabled: true };
    const loginOverlay = document.getElementById('login-overlay');
    const loginForm = document.getElementById('login-form');
    const loginError = document.getElementById('login-error');
    const authChip = document.getElementById('auth-chip');
    const authUser = document.getElementById('auth-user');
    const loginOpenBtn = document.getElementById('btn-login-open');
    let loginPromptShown = false;

    function renderAuth() {
        if (authChip) authChip.hidden = !authState.user;
        if (authUser) authUser.textContent = authState.user || '';
        if (loginOpenBtn) loginOpenBtn.hidden = !!authState.user || !authState.loginEnabled;
    }

    function showLogin(message) {
        if (!loginOverlay) return;
        if (loginError) {
            loginError.textContent = message || '';
            loginError.hidden = !message;
        }
        loginOverlay.hidden = false;
        loginPromptShown = true;
        const user = document.getElementById('login-user');
        if (user) user.focus();
    }

    function hideLogin() {
        if (loginOverlay) loginOverlay.hidden = true;
    }

    async function checkAuth() {
        try {
            const res = await fetch('/api/me');
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const data = await res.json();
            authState.user = data.authenticated ? data.user : null;
            authState.loginEnabled = !!data.login_enabled;
        } catch (err) {
            console.error('無法取得登入狀態', err);
            authState.user = null;
        }
        renderAuth();
        if (!authState.user && authState.loginEnabled && !loginPromptShown) showLogin();
    }

    if (loginForm) {
        loginForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const username = document.getElementById('login-user').value.trim();
            const password = document.getElementById('login-pass').value;
            const btn = document.getElementById('btn-login');
            if (btn) btn.disabled = true;
            try {
                const res = await fetch('/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    const fallback = res.status === 429 ? I18N.t('login_failed_throttled') : I18N.t('login_failed');
                    showLogin(typeof data.detail === 'string' ? data.detail : fallback);
                    return;
                }
                authState.user = data.user;
                renderAuth();
                hideLogin();
                document.getElementById('login-pass').value = '';
                addChat(I18N.t('logged_in', { user: data.user }), 'ai');
            } catch (err) {
                console.error('登入失敗', err);
                showLogin(I18N.t('login_unreachable'));
            } finally {
                if (btn) btn.disabled = false;
            }
        });
    }

    const skipBtn = document.getElementById('btn-login-skip');
    if (skipBtn) {
        skipBtn.addEventListener('click', () => {
            hideLogin();
            addChat(I18N.t('browse_mode'), 'ai');
        });
    }
    if (loginOpenBtn) loginOpenBtn.addEventListener('click', () => showLogin());

    const logoutBtn = document.getElementById('btn-logout');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', async () => {
            try { await fetch('/api/logout', { method: 'POST' }); } catch (err) { console.error('登出失敗', err); }
            authState.user = null;
            renderAuth();
            addChat(I18N.t('logged_out'), 'ai');
        });
    }

    // 需要登入的 POST。401 表示尚未登入或 session 過期，直接跳登入面板
    async function postAuthed(url, body) {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: body === undefined ? undefined : JSON.stringify(body),
        });
        if (res.status === 401) {
            authState.user = null;
            renderAuth();
            showLogin(I18N.t('session_expired'));
            throw new Error('AUTH_REQUIRED');
        }
        if (res.status === 503) throw new Error('AUTH_DISABLED');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
    }

    function describeAuthError(err) {
        if (!err) return null;
        if (err.message === 'AUTH_REQUIRED') return I18N.t('auth_required');
        if (err.message === 'AUTH_DISABLED') return I18N.t('auth_disabled');
        return null;
    }

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
    async function loadHazardZones() {
        try {
            const res = await fetch('/api/hazard_zones');
            if (!res.ok) return;
            const data = await res.json();
            L.geoJSON(data, {
                style: { color: '#ff9f43', weight: 1, opacity: 0.7, fillColor: '#ff9f43', fillOpacity: 0.18 },
                onEachFeature: (f, layer) => {
                    const p = f.properties || {};
                    layer.bindPopup(() =>
                        `<div class="popup-name">${I18N.t('debris_stream')} ${p.debris_no || ''}</div>`
                        + `<div class="popup-row"><span>${I18N.t('popup_location')}</span><span>${p.county || ''}${p.town || ''}${p.village || ''}</span></div>`
                        + `<div class="popup-row"><span>${I18N.t('popup_risk')}</span><span>${p.risk || '-'}</span></div>`
                        + `<div class="popup-row"><span>${I18N.t('popup_households')}</span><span>${p.households ?? '-'} ${I18N.t('households_unit')}</span></div>`
                    );
                },
            }).addTo(map);
        } catch (err) {
            // 疊圖只是輔助資訊，載不到不影響其他功能
            console.error('災害潛勢區域載入失敗', err);
        }
    }
    loadHazardZones();

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

    // ── Canvas 人群動畫 ──────────────────────────────────────
    const canvas = document.getElementById('crowd-canvas');
    const ctx = canvas.getContext('2d');
    let animationId = null;
    let particles = [];

    function resizeCanvas() {
        const panel = document.getElementById('map-panel');
        canvas.width = panel.clientWidth;
        canvas.height = panel.clientHeight;
    }

    window.addEventListener('resize', resizeCanvas);
    resizeCanvas();

    // 地圖座標 → canvas 像素
    function latLonToCanvas(lat, lon) {
        const point = map.latLngToContainerPoint(L.latLng(lat, lon));
        return { x: point.x, y: point.y };
    }

    // 距離計算
    function distKm(lat1, lon1, lat2, lon2) {
        const R = 6371;
        const dLat = (lat2 - lat1) * Math.PI / 180;
        const dLon = (lon2 - lon1) * Math.PI / 180;
        const a = Math.sin(dLat/2)**2 + Math.cos(lat1*Math.PI/180) * Math.cos(lat2*Math.PI/180) * Math.sin(dLon/2)**2;
        return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    }

    const PEOPLE_PER_DOT = 25;
    // 起點在鄉鎮人口重心附近散開的範圍（公里）；只影響畫面，不影響分配
    const SPAWN_SPREAD_KM = 1.2;

    function gaussRand() {
        let u = 0, v = 0;
        while (!u) u = Math.random();
        while (!v) v = Math.random();
        return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
    }

    function spawnNear(lat, lon, lonScale) {
        for (let tries = 0; tries < 10; tries++) {
            const sLat = lat + gaussRand() * SPAWN_SPREAD_KM / 111;
            const sLon = lon + gaussRand() * SPAWN_SPREAD_KM / lonScale;
            if (isLand(sLat, sLon)) return { lat: sLat, lon: sLon };
        }
        return { lat, lon };
    }

    // Google encoded polyline（precision 5）→ [{lat, lon}]；後端 pgRouting 算出的路線用這個格式傳
    function decodePolyline(str) {
        const points = [];
        let index = 0, lat = 0, lon = 0;
        while (index < str.length) {
            for (const axis of [0, 1]) {
                let result = 0, shift = 0, b;
                do {
                    b = str.charCodeAt(index++) - 63;
                    result |= (b & 0x1f) << shift;
                    shift += 5;
                } while (b >= 0x20);
                const delta = (result & 1) ? ~(result >> 1) : (result >> 1);
                if (axis === 0) lat += delta; else lon += delta;
            }
            points.push({ lat: lat / 1e5, lon: lon / 1e5 });
        }
        return points;
    }

    // 一條折線與每個頂點的累積距離（公里），人群點依移動比例沿線內插
    function measuredRoute(points) {
        const cum = [0];
        for (let i = 1; i < points.length; i++) {
            cum.push(cum[i - 1] + distKm(points[i - 1].lat, points[i - 1].lon, points[i].lat, points[i].lon));
        }
        return { points, cum, km: cum[cum.length - 1] };
    }

    function pointAlong(route, s) {
        const { points, cum } = route;
        if (s <= 0) return points[0];
        let lo = 0, hi = cum.length - 1;
        if (s >= cum[hi]) return points[hi];
        while (hi - lo > 1) {
            const mid = (lo + hi) >> 1;
            if (cum[mid] <= s) lo = mid; else hi = mid;
        }
        const span = cum[hi] - cum[lo];
        const k = span > 0 ? (s - cum[lo]) / span : 0;
        return {
            lat: points[lo].lat + (points[hi].lat - points[lo].lat) * k,
            lon: points[lo].lon + (points[hi].lon - points[lo].lon) * k,
        };
    }

    // 疏散路線圖層：每一筆分配一條線，線寬依人數。算不到道路路線的畫虛線直線，跟真的路線區分開
    let routeLayer = null;

    function clearRoutes() {
        if (routeLayer) { map.removeLayer(routeLayer); routeLayer = null; }
    }

    function flowRoute(f) {
        const from = { lat: f.from_lat, lon: f.from_lon };
        const to = { lat: f.to_lat, lon: f.to_lon };
        // 路線只有路上那段，頭尾補上鄉鎮重心走到路上、離開道路走到避難所的兩小段
        return measuredRoute(f.path ? [from, ...decodePolyline(f.path), to] : [from, to]);
    }

    function drawRoutes(flows, routes) {
        clearRoutes();
        routeLayer = L.layerGroup().addTo(map);
        flows.forEach((f, i) => {
            const vars = { from: f.from, to: f.to.replace(/\[.*?\]\s*/, ''), people: f.people, km: f.road_km };
            L.polyline(routes[i].points.map(p => [p.lat, p.lon]), {
                color: '#ffe14d',
                weight: Math.min(7, 1.5 + Math.sqrt(f.people) / 12),
                opacity: f.path ? 0.55 : 0.35,
                dashArray: f.path ? null : '4, 6',
            }).bindTooltip(I18N.t(f.path ? 'route_tip' : 'route_tip_straight', vars), { sticky: true })
              .addTo(routeLayer);
        });
    }

    // 誰去哪一間、各多少人由後端決定（/api/simulate_disaster 的 evacuation），並已寫入資料庫。
    // 這裡只把每一筆分配拆成人群點畫出來；亂數只用在起點的散布位置，不影響任何數字。
    // 有道路路線的沿路移動，沒有的走直線
    function startCrowdAnimation(plan, finalOccupancy, disasterLat, dotColor) {
        if (animationId) cancelAnimationFrame(animationId);
        particles = [];

        const flows = (plan && plan.flows) || [];
        const placed = (plan && plan.placed) || 0;
        const unplaced = (plan && plan.unplaced) || 0;

        if (flows.length === 0) {
            document.getElementById('sim-progress-wrap').classList.remove('visible');
            document.getElementById('btn-simulate').disabled = false;
            applyOccupancy(finalOccupancy);
            addChat(unplaced > 0 ? I18N.t('all_full') : I18N.t('no_population'), 'ai');
            return;
        }

        const lonScale = 111 * Math.cos(disasterLat * Math.PI / 180);
        const totalDots = Math.min(300, Math.max(8, Math.round(placed / PEOPLE_PER_DOT)));
        const routes = flows.map(flowRoute);
        drawRoutes(flows, routes);

        flows.forEach((f, fi) => {
            // 每筆分配至少一個點；人數平均拆到各點，加總恰好等於分配人數
            const dots = Math.max(1, Math.round(f.people / placed * totalDots));
            const base = Math.floor(f.people / dots);
            const extra = f.people % dots;
            const route = routes[fi];
            for (let i = 0; i < dots; i++) {
                // 散開的起點先走到這條路線的開頭，再沿路線前進
                const spawn = spawnNear(f.from_lat, f.from_lon, lonScale);
                const lead = distKm(spawn.lat, spawn.lon, route.points[0].lat, route.points[0].lon);
                particles.push({
                    spawn, route, lead,
                    targetName: f.to,
                    people: base + (i < extra ? 1 : 0),
                    startTime: null,
                    delay: Math.random() * 8000,
                    duration: 6000 + (lead + route.km) * 400 + Math.random() * 3000,
                    done: false,
                    arrived: false,
                });
            }
        });

        let totalArrived = 0;
        const total = particles.length;

        function animate(now) {
            ctx.clearRect(0, 0, canvas.width, canvas.height);

            let allDone = true;
            particles.forEach(p => {
                if (p.done) return;

                if (p.startTime === null) p.startTime = now;
                const elapsed = now - p.startTime - p.delay;

                if (elapsed < 0) { allDone = false; return; }

                const t = Math.min(1, elapsed / p.duration);
                // easeInOut
                const ease = t < 0.5 ? 2 * t * t : -1 + (4 - 2*t) * t;

                const s = (p.lead + p.route.km) * ease;
                let at;
                if (s < p.lead) {
                    const k = s / p.lead;
                    const head = p.route.points[0];
                    at = { lat: p.spawn.lat + (head.lat - p.spawn.lat) * k, lon: p.spawn.lon + (head.lon - p.spawn.lon) * k };
                } else {
                    at = pointAlong(p.route, s - p.lead);
                }
                const pos = latLonToCanvas(at.lat, at.lon);

                if (t >= 1) {
                    if (!p.arrived) {
                        p.arrived = true;
                        totalArrived++;
                        const shelter = allShelterData.find(s => s.name === p.targetName);
                        if (shelter) {
                            shelterOccupancy[p.targetName] = Math.min(
                                shelter.z,
                                (shelterOccupancy[p.targetName] || 0) + p.people
                            );
                            const m = markers.find(mk => mk.userData.name === p.targetName);
                            if (m) updateMarkerColor(m);
                        }
                    }
                    p.done = true;
                    return;
                }

                allDone = false;

                // 畫人群點
                ctx.beginPath();
                ctx.arc(pos.x, pos.y, 3.5, 0, Math.PI * 2);
                ctx.fillStyle = dotColor;
                ctx.globalAlpha = 0.85;
                ctx.fill();
                ctx.globalAlpha = 1;
            });

            // 更新進度條
            const progress = Math.round((totalArrived / total) * 100);
            document.getElementById('sim-progress-bar').style.width = `${progress}%`;
            document.getElementById('progress-pct').textContent = `${progress}%`;

            if (!allDone) {
                animationId = requestAnimationFrame(animate);
            } else {
                // 動畫結束：畫面改用後端寫入資料庫的數字，跟 AI 助手看到的是同一份
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                document.getElementById('sim-progress-wrap').classList.remove('visible');
                document.getElementById('btn-simulate').disabled = false;
                applyOccupancy(finalOccupancy);
                const count = Object.keys(plan.assigned || {}).length;
                addChat(I18N.t('sim_done', { placed, count })
                    + (unplaced > 0 ? I18N.t('sim_unplaced', { unplaced }) : '')
                    + I18N.t('sim_done_tail'), 'ai');
            }
        }

        animationId = requestAnimationFrame(animate);
    }

    // 以後端回傳的佔用表為準，更新本地狀態、基準值與 marker 顏色
    function applyOccupancy(occupancy) {
        if (!occupancy) return;
        allShelterData.forEach(d => {
            if (d.name in occupancy) {
                d.ppl = occupancy[d.name];
                shelterOccupancy[d.name] = occupancy[d.name];
            }
        });
        markers.forEach(m => updateMarkerColor(m));
    }

    // ── 災害模擬按鈕 ─────────────────────────────────────────
    document.getElementById('btn-simulate').addEventListener('click', async () => {
        if (!authState.user && authState.loginEnabled) {
            addChat(describeAuthError(new Error('AUTH_REQUIRED')), 'ai');
            showLogin();
            return;
        }
        const [lon, lat] = state.loc.split(',').map(Number);
        const radius = parseFloat(document.getElementById('sim-radius').value);
        const type = state.type;

        let color = '#ff3d5c';
        if (type === 'flood') color = '#4da6ff';
        if (type === 'fire') color = '#ff7a3d';

        const dotColor = '#ffe14d';

        if (disasterCircle) map.removeLayer(disasterCircle);
        clearRoutes();
        disasterCircle = L.circle([lat, lon], {
            radius: radius * 1000,
            color,
            fillColor: color,
            fillOpacity: 0.08,
            weight: 2,
            opacity: 0.6,
            dashArray: '6, 4',
        }).addTo(map);

        map.flyTo([lat, lon], 11, { duration: 1 });

        markers.forEach(m => {
            const el = m.getElement()?.querySelector('div');
            if (el) el.style.background = m.baseColor;
        });

        try {
            const result = await postAuthed('/api/simulate_disaster', { lat, lon, radius, type });

            if (result.impacted_count === 0) {
                addChat(I18N.t('no_shelter_in_range'), 'ai');
                return;
            }

            const typeLabel = I18N.t(`disaster_${type}`);
            addChat(I18N.t('sim_start', { type: typeLabel, radius, count: result.impacted_count }), 'ai');
            const pop = result.population;
            if (pop) {
                // 中文用「萬」；英文直接寫完整數字（175,000）
                const fmt = n => I18N.lang === 'en'
                    ? I18N.t('people_approx', { n: Math.round(n).toLocaleString('en') })
                    : n >= 10000 ? I18N.t('people_approx_wan', { n: (n / 10000).toFixed(1) }) : I18N.t('people_approx', { n });
                const gap = pop.shortfall > 0 ? I18N.t('sim_gap', { gap: fmt(pop.shortfall) }) : I18N.t('sim_enough');
                addChat(pop.fallback_estimate
                    ? I18N.t('sim_fallback_pop', { evacuees: fmt(pop.estimated_evacuees), gap })
                    : I18N.t('sim_pop', { covered: fmt(pop.covered_population), evacuees: fmt(pop.estimated_evacuees), gap }), 'ai');
            }

            // 顯示進度條，鎖定按鈕
            document.getElementById('sim-progress-wrap').classList.add('visible');
            document.getElementById('btn-simulate').disabled = true;

            // 重置負載率並開始動畫
            resizeCanvas();
            startCrowdAnimation(result.evacuation, result.occupancy, lat, dotColor);

        } catch (err) {
            console.error('模擬失敗', err);
            addChat(describeAuthError(err) || I18N.t('sim_failed'), 'ai');
        }
    });

    // 清除
    document.getElementById('btn-reset').addEventListener('click', async () => {
        if (disasterCircle) { map.removeLayer(disasterCircle); disasterCircle = null; }
        clearRoutes();
        if (animationId) { cancelAnimationFrame(animationId); animationId = null; }
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        particles = [];
        document.getElementById('sim-progress-wrap').classList.remove('visible');
        document.getElementById('btn-simulate').disabled = false;
        document.getElementById('sim-progress-bar').style.width = '0%';
        document.getElementById('progress-pct').textContent = '0%';
        try {
            const data = await postAuthed('/api/reset_simulation');
            applyOccupancy(data.occupancy);
            addChat(I18N.t('reset_done'), 'ai');
        } catch (err) {
            // 後端沒回應時至少把畫面還原成上次載入的值
            console.error('重置模擬失敗', err);
            allShelterData.forEach(d => { shelterOccupancy[d.name] = d.ppl || 0; });
            markers.forEach(m => updateMarkerColor(m));
            addChat(describeAuthError(err) || I18N.t('reset_partial'), 'ai');
        }
    });

    // 地圖移動時重繪 canvas 粒子位置
    map.on('move zoom', () => {
        resizeCanvas();
    });

    function addChat(m, t) {
        const c = document.getElementById('chat-container');
        const d = document.createElement('div');
        d.className = `msg msg-${t}`;
        d.textContent = m;
        c.appendChild(d);
        c.scrollTop = c.scrollHeight;
        return d;
    }

    // 一次只送一個問題：等回答期間鎖住輸入框，連按 Enter 不會對後端塞進多個生成請求
    let chatPending = false;

    function setChatBusy(busy) {
        chatPending = busy;
        const input = document.getElementById('user-input');
        const btn = document.querySelector('.send-btn');
        if (input) input.disabled = busy;
        if (btn) btn.disabled = busy;
        if (!busy && input) input.focus();
    }

    // 讀後端的 SSE：每個事件是一行 data: {json}，事件之間以空白行分隔。
    // 跨 chunk 的半個事件要留在 buffer 裡等下一段，不能直接丟掉
    async function* readChatEvents(body) {
        const reader = body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let cut;
            while ((cut = buffer.indexOf('\n\n')) !== -1) {
                const frame = buffer.slice(0, cut);
                buffer = buffer.slice(cut + 2);
                for (const line of frame.split('\n')) {
                    if (!line.startsWith('data:')) continue;
                    try {
                        yield JSON.parse(line.slice(5));
                    } catch {
                        console.error('無法解析的 SSE 事件', line);
                    }
                }
            }
        }
    }

    // 列舉題生成要一分鐘以上，所以邊收邊貼：第一個字到了就把「思考中」換成答案泡泡
    window.sendChat = async function(v) {
        v = (v || '').trim();
        if (!v || chatPending) return;
        setChatBusy(true);
        addChat(v, 'user');
        document.getElementById('user-input').value = '';
        const container = document.getElementById('chat-container');
        const loadingEl = addChat(I18N.t('thinking'), 'loading');
        let bubble = null;
        try {
            const res = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: v, lang: I18N.lang })
            });
            if (res.status === 429) {
                // 後端的生成名額滿了（別人正在問），不是連線問題
                const data = await res.json().catch(() => ({}));
                addChat(typeof data.detail === 'string' ? data.detail : I18N.t('busy'), 'ai');
                return;
            }
            if (!res.ok || !res.body) throw new Error('HTTP ' + res.status);
            for await (const event of readChatEvents(res.body)) {
                if (event.type === 'done') break;
                if (!event.text) continue;
                if (!bubble) {
                    loadingEl.remove();
                    bubble = addChat('', 'ai');
                }
                bubble.textContent += event.text;
                container.scrollTop = container.scrollHeight;
            }
            if (!bubble) addChat(I18N.t('no_answer'), 'ai');
        } catch {
            // 串流中途斷線：已經收到的文字留在畫面上，後面補一句說明
            if (bubble) bubble.textContent += I18N.t('interrupted');
            else addChat(I18N.t('connect_failed'), 'ai');
        } finally {
            loadingEl.remove();
            setChatBusy(false);
        }
    };

    const langBtn = document.getElementById('btn-lang');
    if (langBtn) {
        langBtn.addEventListener('click', () => {
            I18N.set(I18N.lang === 'en' ? 'zh' : 'en');
            addChat(I18N.t('lang_switched'), 'ai');
        });
    }

    const retryBtn = document.getElementById('app-error-retry');
    if (retryBtn) {
        retryBtn.addEventListener('click', () => {
            clearLoadError();
            loadShelters();
        });
    }

    checkAuth();
    loadPopulationModel();
    loadShelters();
