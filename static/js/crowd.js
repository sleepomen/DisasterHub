// 疏散動畫：人群點沿著道路路線移動、路線圖層、動畫結束後套用收容人數
// 所有檔案用一般的 <script> 依序載入、共用同一個全域範圍（見 index.html 的順序）；
// 頂層只呼叫同一個檔案或前面檔案定義的函式，其餘都在事件發生時才呼叫

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
// 起點在村里起點（最大聚落）附近散開的範圍（公里）；只影響畫面，不影響分配。
// 以前起點是鄉鎮重心，散開 1.2 公里；村里小得多
const SPAWN_SPREAD_KM = 0.3;

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
    // 路線只有路上那段，頭尾補上村里起點走到路上、離開道路走到避難所的兩小段
    return measuredRoute(f.path ? [from, ...decodePolyline(f.path), to] : [from, to]);
}

function drawRoutes(flows, routes) {
    clearRoutes();
    routeLayer = L.layerGroup().addTo(map);
    flows.forEach((f, i) => {
        const vars = {
            from: f.from, to: f.to.replace(/\[.*?\]\s*/, ''), people: f.people, km: f.road_km, m: f.hazard_m,
            walk: f.walk_min, drive: f.drive_min,
        };
        // 打開時才組字，切換語言後提示也跟著換
        const tip = () => I18N.t(f.path ? 'route_tip' : 'route_tip_straight', vars) + (f.hazard_m ? I18N.t('route_tip_hazard', vars) : '');
        L.polyline(routes[i].points.map(p => [p.lat, p.lon]), {
            // 沒有更安全的路、仍有路段經過災害範圍的路線用橘色標出來
            color: f.hazard_m ? THEME.routeHazard : THEME.route,
            // 淺色地圖上要比以前的深色底粗、不透明一點才看得到
            weight: Math.min(7, 2.4 + Math.sqrt(f.people) / 12),
            opacity: f.path ? 0.8 : 0.5,
            dashArray: f.path ? null : '4, 6',
        }).bindTooltip(tip, { sticky: true })
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

            // 畫人群點：白點加深色外框。路線也是深藍，實心的深藍點走在線上會融在一起看不見
            ctx.beginPath();
            ctx.arc(pos.x, pos.y, 3.5, 0, Math.PI * 2);
            ctx.fillStyle = '#FFFFFF';
            ctx.fill();
            ctx.lineWidth = 1.8;
            ctx.strokeStyle = dotColor;
            ctx.stroke();
        });

        // 更新進度條
        const progress = Math.round((totalArrived / total) * 100);
        document.getElementById('sim-progress-bar').style.width = `${progress}%`;
        document.getElementById('progress-pct').textContent = `${progress}%`;
        document.getElementById('sim-progress-bar-bg').setAttribute('aria-valuenow', String(progress));

        if (!allDone) {
            animationId = requestAnimationFrame(animate);
        } else {
            // 動畫結束：畫面改用後端寫入資料庫的數字，跟 AI 助手看到的是同一份
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            document.getElementById('sim-progress-wrap').classList.remove('visible');
            document.getElementById('btn-simulate').disabled = false;
            applyOccupancy(finalOccupancy);
            const count = Object.keys(plan.assigned || {}).length;
            const cutOff = plan.cut_off || [];
            addChat(I18N.t('sim_done', { placed, count })
                + (unplaced > 0 ? I18N.t('sim_unplaced', { unplaced }) : '')
                + (cutOff.length ? I18N.t('sim_cut_off', { names: cutOff.join(I18N.lang === 'en' ? ', ' : '、') }) : '')
                + ((plan.unreachable || []).length ? I18N.t('sim_unreachable', {
                    names: plan.unreachable.map(n => n.replace(/\[.*?\]\s*/, '')).join(I18N.lang === 'en' ? ', ' : '、'),
                }) : '')
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
