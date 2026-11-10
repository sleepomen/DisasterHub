// 在地圖上點選模擬中心，以及模擬後畫出的村里起點
// 所有檔案用一般的 <script> 依序載入、共用同一個全域範圍（見 index.html 的順序）；
// 頂層只呼叫同一個檔案或前面檔案定義的函式，其餘都在事件發生時才呼叫

// ── 在地圖上點選模擬中心 ─────────────────────────────────
// 以前只能選花蓮市、宜蘭市、台東市三個中心，頭城沿海、太麻里山區這些路網被切斷的情境跑不出來。
// 點一下地圖就把那裡當中心；名稱由後端用最近的村里標上（/api/locate，只是標籤）
let pickedMarker = null;
let picked = null;

function pickedLabel() {
    if (!picked) return '';
    const coords = { lat: picked.lat.toFixed(3), lon: picked.lon.toFixed(3) };
    if (!picked.land) return I18N.t('loc_sea', coords);
    if (!picked.township) return I18N.t('loc_point', coords);
    const name = I18N.lang === 'en'
        ? (picked.village_en || picked.township_en || picked.township)
        : `${picked.township}${picked.village}`;
    return I18N.t('loc_picked', { name });
}

function showPickedCentre() {
    const display = document.getElementById('loc-display');
    // 不再跟著 data-i18n 換字：切換語言時由 pickedLabel() 重組
    delete display.dataset.i18n;
    display.textContent = pickedLabel();
    display.title = pickedLabel();
    document.querySelectorAll('#loc-dropdown .dropdown-option').forEach(opt => {
        opt.classList.remove('selected');
        opt.setAttribute('aria-selected', 'false');
        const check = opt.querySelector('.checkmark');
        if (check) check.remove();
    });
    if (pickedMarker) pickedMarker.setTooltipContent(pickedLabel());
}

function clearPickedCentre() {
    picked = null;
    document.getElementById('loc-display').removeAttribute('title');
    if (pickedMarker) { map.removeLayer(pickedMarker); pickedMarker = null; }
}

map.on('click', async (e) => {
    // 動畫進行中換中心會讓畫面上的圈跟正在跑的分配對不上
    if (document.getElementById('btn-simulate').disabled) return;
    // 點在潛勢範圍、路線、起點上是要看它的說明，不是換中心（這些圖層的點擊會冒泡到地圖）
    const target = e.originalEvent && e.originalEvent.target;
    if (target && target.classList && target.classList.contains('leaflet-interactive')) return;
    const lat = e.latlng.lat;
    const lon = e.latlng.lng;
    state.loc = `${lon.toFixed(5)},${lat.toFixed(5)}`;
    picked = { lat, lon, land: true, township: '', village: '' };
    if (!pickedMarker) {
        pickedMarker = L.circleMarker([lat, lon], {
            radius: 7, color: THEME.picked, weight: 2.5, fillColor: '#FFFFFF', fillOpacity: 0.9,
        }).bindTooltip('', { direction: 'top', offset: [0, -6] }).addTo(map);
    } else {
        pickedMarker.setLatLng([lat, lon]);
    }
    showPickedCentre();
    try {
        const res = await fetch(`/api/locate?lat=${lat}&lon=${lon}`);
        // 使用者連點時，只採用最後一次點的結果
        if (res.ok && picked && picked.lat === lat && picked.lon === lon) {
            picked = await res.json();
            picked.lat = lat;
            picked.lon = lon;
            showPickedCentre();
        }
    } catch (err) {
        console.error('查詢選點名稱失敗', err);
    }
});

// ── 疏散起點：這次模擬受影響的村里（每個村里一個起點，圈越大受影響人口越多）──
let originLayer = null;

function clearOrigins() {
    if (originLayer) { map.removeLayer(originLayer); originLayer = null; }
}

function drawOrigins(villages) {
    clearOrigins();
    originLayer = L.layerGroup().addTo(map);
    (villages || []).forEach(v => {
        L.circleMarker([v.lat, v.lon], {
            radius: Math.min(9, 2.5 + Math.sqrt(v.weight) / 15),
            color: THEME.origin, weight: 1.5, opacity: 0.9, fillColor: '#FFFFFF', fillOpacity: 0.5,
        }).bindTooltip(() => I18N.t('origin_tip', {
            name: I18N.lang === 'en' ? (v.name_en || v.name) : v.name,
            n: Math.round(v.weight).toLocaleString(I18N.lang === 'en' ? 'en' : 'zh-TW'),
        }), { sticky: true }).addTo(originLayer);
    });
}
