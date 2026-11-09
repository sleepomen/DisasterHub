// 執行模擬與清除圖層的按鈕
// 所有檔案用一般的 <script> 依序載入、共用同一個全域範圍（見 index.html 的順序）；
// 頂層只呼叫同一個檔案或前面檔案定義的函式，其餘都在事件發生時才呼叫

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

    const color = THEME.impact[type] || THEME.impact.earthquake;
    const dotColor = THEME.crowd;

    if (disasterCircle) map.removeLayer(disasterCircle);
    clearRoutes();
    clearOrigins();
    disasterCircle = L.circle([lat, lon], {
        radius: radius * 1000,
        color,
        fillColor: color,
        fillOpacity: 0.08,
        weight: 2,
        opacity: 0.6,
        dashArray: '6, 4',
        // 不接收點擊：模擬過一次之後，在圈內點一下仍然是選新的中心
        interactive: false,
    }).addTo(map);

    map.flyTo([lat, lon], 11, { duration: 1 });

    markers.forEach(m => {
        const el = m.getElement()?.querySelector('div');
        if (el) el.style.background = m.baseColor;
    });

    try {
        const result = await postAuthed('/api/simulate_disaster', { lat, lon, radius, type, mode: state.mode });

        if (result.impacted_count === 0) {
            addChat(I18N.t('no_shelter_in_range'), 'ai');
            return;
        }

        if (result.population) drawOrigins(result.population.villages);
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
    clearOrigins();
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
