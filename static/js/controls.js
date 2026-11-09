// 左側面板的下拉選單與目前的選項（state）；HTML 的 onclick 直接呼叫這裡的函式
// 所有檔案用一般的 <script> 依序載入、共用同一個全域範圍（見 index.html 的順序）；
// 頂層只呼叫同一個檔案或前面檔案定義的函式，其餘都在事件發生時才呼叫

// 地圖上畫的顏色（標記、路線、警戒範圍、選點…），跟 style.css 的變數是同一組設計：
// 米色底、深藍主色；紅色只留給危險，琥珀色給注意。2026-11-09 從深色 HUD 風格改過來
const THEME = {
    accent: '#1F3A5F',
    accentTrack: '#DDD4C3',
    hualienTaitung: '#2F6F73',      // 花蓮、台東的避難所
    yilan: '#4A6FA5',               // 宜蘭的避難所
    loadLow: '#3E7B5A',             // 收容率 < 50%
    loadMid: '#C08A1E',             // 50%～80%
    loadHigh: '#B23A2E',            // ≥ 80%
    crowd: '#1F3A5F',               // 疏散動畫的人群點
    route: '#1F3A5F',               // 疏散路線
    routeHazard: '#B8651E',         // 仍經過災害範圍的路線
    origin: '#5E574C',              // 村里起點
    picked: '#B23A2E',              // 地圖上點選的模擬中心
    zone: '#9A7B4F',                // 土石流潛勢範圍（沒有警戒）
    alertRed: '#B23A2E',            // 紅色警戒：範圍內道路封閉
    alertYellow: '#D19A1E',         // 黃色警戒：範圍內道路盡量繞行
    impact: { earthquake: '#B23A2E', flood: '#2F6FA5', fire: '#B8651E' },
};

const state = { city: 'all', loc: '121.60,23.98', type: 'earthquake', mode: 'walk' };

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
    if (key === 'loc') clearPickedCentre();
}

document.addEventListener('click', () => {
    document.querySelectorAll('.dropdown-menu').forEach(m => m.classList.remove('open'));
});
