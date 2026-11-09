// 半徑滑桿、語言切換、重新載入，以及頁面載入時要做的事；最後載入
// 所有檔案用一般的 <script> 依序載入、共用同一個全域範圍（見 index.html 的順序）；
// 頂層只呼叫同一個檔案或前面檔案定義的函式，其餘都在事件發生時才呼叫

// 半徑滑桿：標籤與填色跟著數值走。1e2791d（加登入）時被不小心刪掉，
// 之後拖滑桿標籤一直停在「10 km」，送出去的半徑卻是新的值（2026-11-08 截圖時發現）
const radiusInput = document.getElementById('sim-radius');
function showRadius() {
    const v = radiusInput.value;
    document.getElementById('radius-val').textContent = `${v} km`;
    const pct = ((v - radiusInput.min) / (radiusInput.max - radiusInput.min)) * 100;
    radiusInput.style.background = `linear-gradient(to right, ${THEME.accent} ${pct}%, ${THEME.accentTrack} ${pct}%)`;
}
radiusInput.addEventListener('input', showRadius);
showRadius();

const langBtn = document.getElementById('btn-lang');
if (langBtn) {
    langBtn.addEventListener('click', () => {
        I18N.set(I18N.lang === 'en' ? 'zh' : 'en');
        if (picked) showPickedCentre();
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

// 圖例的色塊跟地圖上畫的是同一組顏色（THEME），不在 HTML 裡另外寫一份
document.querySelectorAll('.legend [data-theme]').forEach(el => {
    const color = THEME[el.dataset.theme];
    if (el.dataset.theme === 'crowd') {
        // 跟地圖上的人群點一樣：白點加深色外框
        el.style.background = '#FFFFFF';
        el.style.boxShadow = `0 0 0 2px ${color}`;
    } else if (el.classList.contains('legend-ring') || el.classList.contains('legend-area')) {
        el.style.borderColor = color;
        if (el.classList.contains('legend-area')) el.style.background = `${color}40`;
    } else {
        el.style.background = color;
    }
});
