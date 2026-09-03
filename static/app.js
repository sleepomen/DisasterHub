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

    document.getElementById('sim-radius').addEventListener('input', (e) => {
        const v = e.target.value;
        document.getElementById('radius-val').textContent = `${v} km`;
        const pct = ((v - 1) / 59) * 100;
        e.target.style.background = `linear-gradient(to right, #ffbe2e ${pct}%, rgba(255,190,46,0.15) ${pct}%)`;
    });

    // ── Leaflet ──────────────────────────────────────────────
    const map = L.map('map', { center: [23.9, 121.6], zoom: 9, zoomControl: true });

    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> © <a href="https://carto.com/attributions">CARTO</a>',
        maxZoom: 19,
    }).addTo(map);

    let markers = [];
    let disasterCircle = null;
    let allShelterData = [];

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

    async function loadShelters() {
        const res = await fetch('/api/shelters');
        allShelterData = await res.json();
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

    // 以下是新增的
    const PEOPLE_PER_DOT = 25;
    const EVAC_RATIO = { earthquake: 0.12, flood: 0.22, fire: 0.05 };

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

    function startCrowdAnimation(impactedShelters, disasterLat, disasterLon, radiusKm, dotColor, disasterType) {
        if (animationId) cancelAnimationFrame(animationId);
        particles = [];

        const candidates = impactedShelters.map(s => ({
            ...s,
            remaining: Math.max(0, s.capacity - (shelterOccupancy[s.name] || 0)),
        }));
        const totalRoom = candidates.reduce((sum, s) => sum + s.remaining, 0);
        const lonScale = 111 * Math.cos(disasterLat * Math.PI / 180);

        const covered = POP_CENTERS
            .map(c => ({ ...c, d: distKm(c.lat, c.lon, disasterLat, disasterLon) }))
            .map(c => ({ ...c, weight: c.pop * Math.max(0, Math.min(1, 1.15 - c.d / radiusKm)) }))
            .filter(c => c.weight > 0);
        const coveredPop = covered.reduce((s, c) => s + c.weight, 0);
        const ratio = EVAC_RATIO[disasterType] || 0.12;
        let evacuees = Math.round(coveredPop * ratio);
        if (evacuees === 0) evacuees = Math.round(Math.PI * radiusKm * radiusKm * 2);
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
            }
        }

        animationId = requestAnimationFrame(animate);
    }

    // ── 災害模擬按鈕 ─────────────────────────────────────────
    document.getElementById('btn-simulate').addEventListener('click', async () => {
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
            const res = await fetch('/api/simulate_disaster', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ lat, lon, radius, type })
            });
            const result = await res.json();

            if (result.impacted_count === 0) {
                addChat(`此範圍內沒有避難所，請擴大半徑或更換模擬中心。`, 'ai');
                return;
            }

            const typeLabel = type === 'earthquake' ? '強震' : type === 'flood' ? '淹水' : '火災';
            addChat(`模擬啟動：${typeLabel}，半徑 ${radius} km，${result.impacted_count} 個避難所受影響，開始疏散動畫…`, 'ai');

            // 顯示進度條，鎖定按鈕
            document.getElementById('sim-progress-wrap').classList.add('visible');
            document.getElementById('btn-simulate').disabled = true;

            // 重置負載率並開始動畫
            resizeCanvas();
            startCrowdAnimation(result.impacted_shelters, lat, lon, radius, dotColor, type);

        } catch {
            addChat(`模擬失敗，請確認後端服務正常運行。`, 'ai');
        }
    });

    // 清除
    document.getElementById('btn-reset').addEventListener('click', () => {
        if (disasterCircle) { map.removeLayer(disasterCircle); disasterCircle = null; }
        if (animationId) { cancelAnimationFrame(animationId); animationId = null; }
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        particles = [];
        allShelterData.forEach(d => { shelterOccupancy[d.name] = d.ppl || 0; });
        markers.forEach(m => updateMarkerColor(m));
        document.getElementById('sim-progress-wrap').classList.remove('visible');
        document.getElementById('btn-simulate').disabled = false;
        document.getElementById('sim-progress-bar').style.width = '0%';
        document.getElementById('progress-pct').textContent = '0%';
        fetch('/api/reset_simulation', { method: 'POST' }).catch(() => {});
        addChat("已清除模擬圖層與疏散動畫。", "ai");
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

    window.sendChat = async function(v) {
        if (!v) return;
        addChat(v, 'user');
        document.getElementById('user-input').value = '';
        const loadingEl = addChat('AI 思考中…', 'loading');
        try {
            const res = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: v })
            });
            const data = await res.json();
            loadingEl.remove();
            addChat(data.reply, 'ai');
        } catch {
            loadingEl.remove();
            addChat('連線失敗，請稍後再試', 'ai');
        }
    };

    loadShelters();
