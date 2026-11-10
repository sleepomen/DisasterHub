// 半徑滑桿、語言切換、重新載入，以及頁面載入時要做的事；最後載入
// 所有檔案用一般的 <script> 依序載入、共用同一個全域範圍（見 index.html 的順序）；
// 頂層只呼叫同一個檔案或前面檔案定義的函式，其餘都在事件發生時才呼叫

// 半徑滑桿：標籤與填色跟著數值走。1e2791d（加登入）時被不小心刪掉，
// 之後拖滑桿標籤一直停在「10 km」，送出去的半徑卻是新的值（2026-11-08 截圖時發現）
const radiusInput = document.getElementById('sim-radius');
function showRadius() {
    const v = radiusInput.value;
    document.getElementById('radius-val').textContent = `${v} km`;
    radiusInput.setAttribute('aria-valuetext', `${v} km`);
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

// ── 手機：兩個面板預設收起，從底部的分頁打開 ─────────────────
// body 的 data-sheet 決定現在打開哪一個（'sim'、'chat' 或空的），樣式在 style.css 的 @media (max-width: 760px)。
// 桌機上分頁不顯示、面板一直開著，這些狀態不影響
const MOBILE = window.matchMedia('(max-width: 760px)');
const SHEET_TABS = { sim: document.getElementById('tab-sim'), chat: document.getElementById('tab-chat') };

function setSheet(name) {
    document.body.dataset.sheet = name || '';
    for (const [key, tab] of Object.entries(SHEET_TABS)) {
        if (tab) tab.setAttribute('aria-expanded', String(key === name));
    }
}

for (const [key, tab] of Object.entries(SHEET_TABS)) {
    if (tab) tab.addEventListener('click', () => setSheet(document.body.dataset.sheet === key ? '' : key));
}
// 按下執行模擬後收起面板，動畫才看得到
document.getElementById('btn-simulate').addEventListener('click', () => {
    if (MOBILE.matches) setSheet('');
});
document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && MOBILE.matches && document.body.dataset.sheet && !document.querySelector('.dropdown-menu.open')) {
        setSheet('');
    }
});
setSheet('');
// 手機上圖例預設收合，不然左下角一大塊地圖被蓋住
if (MOBILE.matches) document.getElementById('legend').open = false;
