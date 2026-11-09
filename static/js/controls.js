// 左側面板的下拉選單與目前的選項（state）；HTML 的 onclick 直接呼叫這裡的函式
// 所有檔案用一般的 <script> 依序載入、共用同一個全域範圍（見 index.html 的順序）；
// 頂層只呼叫同一個檔案或前面檔案定義的函式，其餘都在事件發生時才呼叫

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
