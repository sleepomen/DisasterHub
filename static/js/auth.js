// 登入狀態、登入視窗，以及要帶 session 的 POST（postAuthed）
// 所有檔案用一般的 <script> 依序載入、共用同一個全域範圍（見 index.html 的順序）；
// 頂層只呼叫同一個檔案或前面檔案定義的函式，其餘都在事件發生時才呼叫

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
