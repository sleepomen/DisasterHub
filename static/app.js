    const state = { city: 'all', loc: '121.60,23.98', type: 'earthquake' };

    function toggleDropdown(e, id) {
        e.stopPropagation();
        document.querySelectorAll('.dropdown-menu').forEach(m => {
            if (m.id !== id) m.classList.remove('open');
        });
        document.getElementById(id).classList.toggle('open');
    }

    function selectOption(e, key, value, label) {
        e.stopPropagation();
        state[key] = value;
        document.getElementById(`${key}-display`).textContent = label;
        const menu = document.getElementById(`${key}-dropdown`);
        menu.querySelectorAll('.dropdown-option').forEach(opt => {
            const isSelected = opt.textContent.replace('✓','').trim() === label;
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
                    const fallback = res.status === 429 ? '登入失敗次數過多，請稍後再試。' : '登入失敗。';
                    showLogin(typeof data.detail === 'string' ? data.detail : fallback);
                    return;
                }
                authState.user = data.user;
                renderAuth();
                hideLogin();
                document.getElementById('login-pass').value = '';
                addChat(`已以 ${data.user} 登入，可執行模擬與回寫收容人數。`, 'ai');
            } catch (err) {
                console.error('登入失敗', err);
                showLogin('連線失敗，請確認後端服務正常運行。');
            } finally {
                if (btn) btn.disabled = false;
            }
        });
    }

    const skipBtn = document.getElementById('btn-login-skip');
    if (skipBtn) {
        skipBtn.addEventListener('click', () => {
            hideLogin();
            addChat('目前為瀏覽模式：可查看地圖與問答，執行模擬前需要登入。', 'ai');
        });
    }
    if (loginOpenBtn) loginOpenBtn.addEventListener('click', () => showLogin());

    const logoutBtn = document.getElementById('btn-logout');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', async () => {
            try { await fetch('/api/logout', { method: 'POST' }); } catch (err) { console.error('登出失敗', err); }
            authState.user = null;
            renderAuth();
            addChat('已登出，目前為瀏覽模式。', 'ai');
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
            showLogin('登入已過期或尚未登入，請重新登入。');
            throw new Error('AUTH_REQUIRED');
        }
        if (res.status === 503) throw new Error('AUTH_DISABLED');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
    }

    function describeAuthError(err) {
        if (!err) return null;
        if (err.message === 'AUTH_REQUIRED') return '請先登入，才能執行模擬或回寫收容人數。';
        if (err.message === 'AUTH_DISABLED') return '伺服器未設定管理者帳號，模擬與收容人數寫入功能已停用，請聯絡管理者。';
        return null;
    }

    // ── Leaflet ──────────────────────────────────────────────
    const map = L.map('map', { center: [23.9, 121.6], zoom: 9, zoomControl: true });

    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> © <a href="https://carto.com/attributions">CARTO</a>',
        maxZoom: 19,
    }).addTo(map);

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
            showLoadError('無法載入避難所資料，請確認後端服務與資料庫是否正常。');
            addChat('無法載入避難所資料，地圖目前是空的。請確認後端與資料庫狀態後按「重新載入」。', 'ai');
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
            const size = Math.max(12, Math.min(26, d.z / 80));
            const occ = shelterOccupancy[d.name] || 0;
            const rate = occ / d.z;

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

    function buildPopup(d) {
        const occ = shelterOccupancy[d.name] || 0;
        const remaining = Math.max(0, d.z - occ);
        const rate = Math.min(100, Math.round(occ / d.z * 100));
        const barColor = rate < 50 ? '#2ee6a8' : rate < 80 ? '#ff7a3d' : '#ff3d5c';
        return `
            <div class="popup-name">${d.name.replace(/\[.*?\]\s*/,'')}</div>
            <div class="popup-row"><span>總容量</span><span>${d.z} 人</span></div>
            <div class="popup-row"><span>目前入住</span><span>${occ} 人</span></div>
            <div class="popup-row"><span>剩餘空間</span><span>${remaining} 人</span></div>
            <div class="popup-row"><span>負載率</span><span style="color:${barColor};font-weight:600">${rate}%</span></div>
            <div class="popup-bar"><div class="popup-bar-fill" style="width:${rate}%;background:${barColor}"></div></div>
        `;
    }

    function updateMarkerColor(markerObj) {
        const d = markerObj.userData;
        const occ = shelterOccupancy[d.name] || 0;
        const rate = occ / d.z;
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

    // 找最近的避難所
    function findNearestShelter(lat, lon, impactedShelters) {
        let best = null, bestDist = Infinity;
        impactedShelters.forEach(s => {
            const d = distKm(lat, lon, s.lat, s.lon);
            if (d < bestDist) { bestDist = d; best = s; }
        });
        return best;
    }

    const PEOPLE_PER_DOT = 25;

    function gaussRand() {
        let u = 0, v = 0;
        while (!u) u = Math.random();
        while (!v) v = Math.random();
        return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
    }

    function pickShelterFor(lat, lon, candidates, planned) {
        let best = null, bestScore = Infinity;
        candidates.forEach(s => {
            const used = planned[s.name] || 0;
            if (s.remaining - used <= 0) return;
            const score = distKm(lat, lon, s.lat, s.lon) * (1 + used / s.remaining);
            if (score < bestScore) { bestScore = score; best = s; }
        });
        return best;
    }

    // population 來自 /api/simulate_disaster：後端已算好圈內人口與疏散需求，這裡只負責把人畫出來
    function startCrowdAnimation(impactedShelters, disasterLat, disasterLon, radiusKm, dotColor, population) {
        if (animationId) cancelAnimationFrame(animationId);
        particles = [];

        const candidates = impactedShelters.map(s => ({
            ...s,
            remaining: Math.max(0, s.capacity - (shelterOccupancy[s.name] || 0)),
        }));
        const totalRoom = candidates.reduce((sum, s) => sum + s.remaining, 0);
        const lonScale = 111 * Math.cos(disasterLat * Math.PI / 180);

        const covered = (population && population.townships) || [];
        const coveredPop = covered.reduce((s, c) => s + c.weight, 0);
        let evacuees = (population && population.estimated_evacuees) || Math.round(Math.PI * radiusKm * radiusKm * 2);
        evacuees = Math.min(totalRoom, evacuees);

        const totalDots = Math.min(300, Math.max(8, Math.round(evacuees / PEOPLE_PER_DOT)));
        const peoplePerDot = Math.max(1, Math.round(evacuees / totalDots));
        const planned = {};

        function pickCenter() {
            let r = Math.random() * coveredPop;
            for (const c of covered) {
                r -= c.weight;
                if (r <= 0) return c;
            }
            return covered[covered.length - 1];
        }

        function sampleSpawn() {
            for (let tries = 0; tries < 15; tries++) {
                let lat, lon;
                if (covered.length) {
                    const c = pickCenter();
                    const spreadKm = Math.min(3.2, 0.7 + Math.sqrt(c.pop) / 260);
                    lat = c.lat + gaussRand() * spreadKm / 111;
                    lon = c.lon + gaussRand() * spreadKm / lonScale;
                } else {
                    const angle = Math.random() * 2 * Math.PI;
                    const r = Math.sqrt(Math.random()) * radiusKm;
                    lat = disasterLat + (r / 111) * Math.cos(angle);
                    lon = disasterLon + (r / lonScale) * Math.sin(angle);
                }
                if (distKm(lat, lon, disasterLat, disasterLon) <= radiusKm && isLand(lat, lon)) {
                    return { lat, lon };
                }
            }
            return null;
        }

        for (let i = 0; i < totalDots; i++) {
            const spawn = sampleSpawn();
            if (!spawn) continue;
            const shelter = pickShelterFor(spawn.lat, spawn.lon, candidates, planned);
            if (!shelter) break;
            planned[shelter.name] = (planned[shelter.name] || 0) + peoplePerDot;

            const delay = Math.random() * 8000;
            const duration = 6000 + distKm(spawn.lat, spawn.lon, shelter.lat, shelter.lon) * 400 + Math.random() * 3000;

            particles.push({
                startLat: spawn.lat, startLon: spawn.lon,
                targetLat: shelter.lat,
                targetLon: shelter.lon,
                targetName: shelter.name,
                people: peoplePerDot,
                startTime: null,
                delay,
                duration,
                done: false,
                color: dotColor,
                arrived: false,
            });
        }

        if (particles.length === 0) {
            document.getElementById('sim-progress-wrap').classList.remove('visible');
            document.getElementById('btn-simulate').disabled = false;
            addChat(totalRoom === 0
                ? '範圍內的避難所皆已滿載，無法再安置人群。'
                : '此範圍內幾乎沒有可疏散的人口（多為海域或無人山區），未產生疏散人流。', 'ai');
            return;
        }

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

                const lat = p.startLat + (p.targetLat - p.startLat) * ease;
                const lon = p.startLon + (p.targetLon - p.startLon) * ease;
                const pos = latLonToCanvas(lat, lon);

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
                // 動畫結束
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                document.getElementById('sim-progress-wrap').classList.remove('visible');
                document.getElementById('btn-simulate').disabled = false;
                const movedPeople = particles.reduce((sum, p) => sum + (p.people || 0), 0);
                addChat(`疏散模擬完成！共約 ${movedPeople} 人（${total} 批次）已移入避難所，請查看各避難所目前負載率。`, 'ai');
                syncOccupancy(candidates.map(s => s.name));
            }
        }

        animationId = requestAnimationFrame(animate);
    }

    // 動畫只改了瀏覽器裡的數字，要回寫後端資料庫與向量索引，AI 的回答才會跟畫面一致
    async function syncOccupancy(names) {
        const occupancy = names
            .filter(name => name in shelterOccupancy)
            .map(name => ({ name, current_ppl: Math.round(shelterOccupancy[name]) }));
        if (occupancy.length === 0) return;
        try {
            const data = await postAuthed('/api/occupancy', { occupancy });
            applyOccupancy(data.occupancy);
            addChat(`已將 ${data.updated} 個避難所的收容人數同步至後端，AI 助手現在會依最新負載回答。`, 'ai');
        } catch (err) {
            console.error('收容人數回寫失敗', err);
            addChat(describeAuthError(err) || '收容人數回寫後端失敗，AI 助手的回答可能與地圖不一致。', 'ai');
        }
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
                addChat(`此範圍內沒有避難所，請擴大半徑或更換模擬中心。`, 'ai');
                return;
            }

            const typeLabel = type === 'earthquake' ? '強震' : type === 'flood' ? '淹水' : '火災';
            addChat(`模擬啟動：${typeLabel}，半徑 ${radius} km，${result.impacted_count} 個避難所受影響，開始疏散動畫…`, 'ai');
            const pop = result.population;
            if (pop) {
                const fmt = n => n >= 10000 ? `約 ${(n / 10000).toFixed(1)} 萬人` : `約 ${n} 人`;
                const gap = pop.shortfall > 0 ? `，收容缺口 ${fmt(pop.shortfall)}` : '，範圍內避難所足以安置';
                addChat(pop.fallback_estimate
                    ? `範圍內無主要聚落，以面積保底估算需疏散 ${fmt(pop.estimated_evacuees)}${gap}。`
                    : `範圍涵蓋人口 ${fmt(pop.covered_population)}，預估需疏散 ${fmt(pop.estimated_evacuees)}${gap}。`, 'ai');
            }

            // 顯示進度條，鎖定按鈕
            document.getElementById('sim-progress-wrap').classList.add('visible');
            document.getElementById('btn-simulate').disabled = true;

            // 重置負載率並開始動畫
            resizeCanvas();
            startCrowdAnimation(result.impacted_shelters, lat, lon, radius, dotColor, result.population);

        } catch (err) {
            console.error('模擬失敗', err);
            addChat(describeAuthError(err) || `模擬失敗，請確認後端服務正常運行。`, 'ai');
        }
    });

    // 清除
    document.getElementById('btn-reset').addEventListener('click', async () => {
        if (disasterCircle) { map.removeLayer(disasterCircle); disasterCircle = null; }
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
            addChat('已清除模擬圖層與疏散動畫，各避難所收容人數已還原。', 'ai');
        } catch (err) {
            // 後端沒回應時至少把畫面還原成上次載入的值
            console.error('重置模擬失敗', err);
            allShelterData.forEach(d => { shelterOccupancy[d.name] = d.ppl || 0; });
            markers.forEach(m => updateMarkerColor(m));
            addChat(describeAuthError(err) || '已清除模擬圖層，但後端收容人數重置失敗，請稍後再試。', 'ai');
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

    window.sendChat = async function(v) {
        v = (v || '').trim();
        if (!v || chatPending) return;
        setChatBusy(true);
        addChat(v, 'user');
        document.getElementById('user-input').value = '';
        const loadingEl = addChat('AI 思考中…', 'loading');
        try {
            const res = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: v })
            });
            const data = await res.json().catch(() => ({}));
            loadingEl.remove();
            if (res.status === 429) {
                // 後端的生成名額滿了（別人正在問），不是連線問題
                addChat(typeof data.detail === 'string' ? data.detail : 'AI 助手正在回答其他問題，請稍後再試。', 'ai');
                return;
            }
            if (!res.ok) throw new Error('HTTP ' + res.status);
            addChat(data.reply, 'ai');
        } catch {
            loadingEl.remove();
            addChat('連線失敗，請稍後再試', 'ai');
        } finally {
            setChatBusy(false);
        }
    };

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
