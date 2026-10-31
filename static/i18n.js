// 介面語言（中文 / English）。第一階段只翻介面與回答：問題仍以中文最準，
// 避難所名稱與地址保留中文（資料沒有英文名稱，現場招牌也是中文）。
// 頁面上的固定文字用 data-i18n（文字）、data-i18n-placeholder（輸入框提示）標記，切換時一次換掉；
// app.js 動態產生的訊息用 I18N.t(key, vars)
(function () {
    const STORAGE_KEY = 'disasterhub.lang';

    const DICT = {
        zh: {
            live: '即時監測中', stat_shelters: '避難所', stat_capacity: '總容量',
            btn_logout: '登出', btn_login: '登入', btn_retry: '重新載入', lang_toggle: 'EN',
            legend_hualien_taitung: '花蓮 / 台東避難所', legend_yilan: '宜蘭避難所', legend_impact: '受災影響範圍',
            legend_crowd: '疏散中人群', legend_route: '疏散路線', legend_debris: '土石流潛勢影響範圍',
            section_area: '觀測區域', label_area: '地區',
            city_all: '全東部區域', city_yilan: '宜蘭', city_hualien: '花蓮', city_taitung: '台東',
            section_sim: '災害模擬', label_center: '模擬中心',
            loc_hualien: '花蓮市中心', loc_yilan: '宜蘭市中心', loc_taitung: '台東市中心',
            label_type: '災害類型', type_earthquake: '強震模擬', type_flood: '淹水模擬', type_fire: '火災模擬',
            label_radius: '受災半徑', sim_progress: '人群疏散模擬中',
            btn_simulate: '執行空間模擬', btn_reset: '清除所有圖層',
            chat_title: 'AI 決策助手', chat_subtitle: 'RAG · PostGIS 空間查詢 · 本地 LLM',
            chat_ready: '系統就緒。請設定模擬參數以分析潛在風險。',
            chat_placeholder: '詢問避難所資訊…',
            login_title: '登入', login_sub: '執行模擬與回寫收容人數需要管理者帳號。不登入仍可瀏覽地圖與使用問答。',
            login_user: '帳號', login_pass: '密碼', login_skip: '先瀏覽地圖',
            login_failed_throttled: '登入失敗次數過多，請稍後再試。', login_failed: '登入失敗。',
            logged_in: '已以 {user} 登入，可執行模擬與回寫收容人數。',
            login_unreachable: '連線失敗，請確認後端服務正常運行。',
            browse_mode: '目前為瀏覽模式：可查看地圖與問答，執行模擬前需要登入。',
            logged_out: '已登出，目前為瀏覽模式。',
            session_expired: '登入已過期或尚未登入，請重新登入。',
            auth_required: '請先登入，才能執行模擬或回寫收容人數。',
            auth_disabled: '伺服器未設定管理者帳號，模擬與收容人數寫入功能已停用，請聯絡管理者。',
            debris_stream: '土石流潛勢溪流', popup_location: '位置', popup_risk: '風險等級',
            popup_households: '保全住戶', households_unit: '戶',
            load_error: '無法載入避難所資料，請確認後端服務與資料庫是否正常。',
            load_error_chat: '無法載入避難所資料，地圖目前是空的。請確認後端與資料庫狀態後按「重新載入」。',
            popup_hazards: '適用災害', popup_venue: '場地', venue_indoor: '室內', venue_outdoor: '室外',
            popup_vulnerable: '避難弱者', vulnerable_ok: '適合安置', popup_phone: '電話', popup_address: '地址',
            popup_capacity: '總容量', not_provided: '未提供', popup_current: '目前收容', popup_remaining: '剩餘空位',
            popup_load: '負載率', people_unit: '人',
            hazard_水災: '水災', hazard_震災: '震災', hazard_土石流: '土石流', hazard_海嘯: '海嘯',
            all_full: '範圍內的避難所皆已滿載，無法再安置人群。',
            no_population: '此範圍內幾乎沒有可疏散的人口（多為海域或無人山區），未產生疏散人流。',
            sim_done: '疏散模擬完成！共 {placed} 人依最近且有空位的原則移入 {count} 間避難所',
            sim_unplaced: '，仍有 {unplaced} 人無法安置',
            sim_done_tail: '。AI 助手已使用同一份分配結果回答。',
            route_tip: '{from} → {to}：{people} 人，道路距離 {km} km',
            route_tip_straight: '{from} → {to}：{people} 人，路網查不到路線，以直線表示',
            route_tip_hazard: '；沒有更安全的路，有 {m} 公尺經過災害範圍',
            sim_cut_off: '；{names} 沒有安全道路可到任何避難所（紅色警戒封路）',
            sim_unreachable: '；{names} 的聯外道路被紅色警戒封閉，沒有分配人員前往',
            popup_alert: '即時警戒', alert_red: '紅色警戒（範圍內道路封閉）', alert_yellow: '黃色警戒（範圍內道路盡量繞行）',
            no_shelter_in_range: '此範圍內沒有避難所，請擴大半徑或更換模擬中心。',
            disaster_earthquake: '強震', disaster_flood: '淹水', disaster_fire: '火災',
            sim_start: '模擬啟動：{type}，半徑 {radius} km，{count} 個避難所受影響，開始疏散動畫…',
            people_approx: '約 {n} 人', people_approx_wan: '約 {n} 萬人',
            sim_gap: '，收容缺口 {gap}', sim_enough: '，範圍內避難所足以安置',
            sim_fallback_pop: '範圍內無主要聚落，以面積保底估算需疏散 {evacuees}{gap}。',
            sim_pop: '範圍涵蓋人口 {covered}，預估需疏散 {evacuees}{gap}。',
            sim_failed: '模擬失敗，請確認後端服務正常運行。',
            reset_done: '已清除模擬圖層與疏散動畫，各避難所收容人數已還原。',
            reset_partial: '已清除模擬圖層，但後端收容人數重置失敗，請稍後再試。',
            thinking: 'AI 思考中…', busy: 'AI 助手正在回答其他問題，請稍後再試。',
            no_answer: 'AI 沒有回應，請稍後再試', interrupted: '（連線中斷）', connect_failed: '連線失敗，請稍後再試',
            lang_switched: '已切換為中文介面。',
        },
        en: {
            live: 'Live', stat_shelters: 'Shelters', stat_capacity: 'Capacity',
            btn_logout: 'Sign out', btn_login: 'Sign in', btn_retry: 'Reload', lang_toggle: '中文',
            legend_hualien_taitung: 'Hualien / Taitung shelters', legend_yilan: 'Yilan shelters', legend_impact: 'Impact area',
            legend_crowd: 'Evacuees on the move', legend_route: 'Evacuation routes', legend_debris: 'Debris-flow impact zones',
            section_area: 'Area', label_area: 'Region',
            city_all: 'All of eastern Taiwan', city_yilan: 'Yilan', city_hualien: 'Hualien', city_taitung: 'Taitung',
            section_sim: 'Disaster simulation', label_center: 'Centre',
            loc_hualien: 'Hualien City centre', loc_yilan: 'Yilan City centre', loc_taitung: 'Taitung City centre',
            label_type: 'Disaster', type_earthquake: 'Earthquake', type_flood: 'Flood', type_fire: 'Fire',
            label_radius: 'Radius', sim_progress: 'Simulating evacuation',
            btn_simulate: 'Run simulation', btn_reset: 'Clear all layers',
            chat_title: 'AI assistant', chat_subtitle: 'RAG · PostGIS · local LLM',
            chat_ready: 'Ready. Set the simulation parameters to analyse potential risk. '
                + 'Answers are in English; questions work best in Chinese, and shelter names stay in Chinese.',
            chat_placeholder: 'Ask about shelters…',
            login_title: 'Sign in to', login_sub: 'Running simulations and writing occupancy back needs an administrator account. '
                + 'Without signing in you can still browse the map and ask questions.',
            login_user: 'Username', login_pass: 'Password', login_skip: 'Browse the map',
            login_failed_throttled: 'Too many failed sign-ins. Please try again later.', login_failed: 'Sign-in failed.',
            logged_in: 'Signed in as {user}. You can run simulations and write occupancy back.',
            login_unreachable: 'Could not connect. Please check that the backend is running.',
            browse_mode: 'Browse mode: you can view the map and ask questions; sign in to run simulations.',
            logged_out: 'Signed out. You are in browse mode.',
            session_expired: 'Your session has expired or you are not signed in. Please sign in again.',
            auth_required: 'Please sign in to run simulations or write occupancy back.',
            auth_disabled: 'No administrator account is configured on the server, so simulations and occupancy '
                + 'writes are disabled. Please contact the administrator.',
            debris_stream: 'Potential debris-flow stream', popup_location: 'Location', popup_risk: 'Risk level',
            popup_households: 'Households protected', households_unit: '',
            load_error: 'Could not load shelter data. Please check the backend and the database.',
            load_error_chat: 'Could not load shelter data, so the map is empty. Check the backend and database, then press "Reload".',
            popup_hazards: 'Designated for', popup_venue: 'Venue', venue_indoor: 'indoor', venue_outdoor: 'outdoor',
            popup_vulnerable: 'Vulnerable evacuees', vulnerable_ok: 'suitable', popup_phone: 'Phone', popup_address: 'Address',
            popup_capacity: 'Capacity', not_provided: 'not provided', popup_current: 'Sheltering', popup_remaining: 'Spaces left',
            popup_load: 'Occupancy', people_unit: '',
            hazard_水災: 'floods', hazard_震災: 'earthquakes', hazard_土石流: 'debris flows', hazard_海嘯: 'tsunamis',
            all_full: 'Every shelter in range is full; no more evacuees can be placed.',
            no_population: 'Almost no one lives in this area (mostly sea or uninhabited mountains), so no evacuation flow was generated.',
            sim_done: 'Evacuation simulated: {placed} people moved into {count} shelters, nearest with free space first',
            sim_unplaced: ', {unplaced} people could not be placed',
            sim_done_tail: '. The AI assistant answers from the same allocation.',
            route_tip: '{from} → {to}: {people} people, {km} km by road',
            route_tip_straight: '{from} → {to}: {people} people; no road route found, shown as a straight line',
            route_tip_hazard: '; no safer road, {m} m pass through a hazard zone',
            sim_cut_off: '; no safe road leads from {names} to any shelter (closed by a red alert)',
            sim_unreachable: '; roads to {names} are closed by a red alert, so no one was sent there',
            popup_alert: 'Alert', alert_red: 'Red alert (roads inside closed)', alert_yellow: 'Yellow alert (roads inside avoided)',
            no_shelter_in_range: 'There are no shelters in this area. Increase the radius or change the centre.',
            disaster_earthquake: 'earthquake', disaster_flood: 'flood', disaster_fire: 'fire',
            sim_start: 'Simulation started: {type}, radius {radius} km, {count} shelters affected. Starting the evacuation animation…',
            people_approx: 'about {n} people',
            sim_gap: ', a shortfall of {gap}', sim_enough: '; the shelters in range can take everyone',
            sim_fallback_pop: 'No major settlement in range; estimated by area, {evacuees} need to evacuate{gap}.',
            sim_pop: 'Population in range: {covered}; an estimated {evacuees} need to evacuate{gap}.',
            sim_failed: 'The simulation failed. Please check that the backend is running.',
            reset_done: 'Simulation layers and the evacuation animation are cleared, and shelter occupancy is restored.',
            reset_partial: 'Simulation layers are cleared, but resetting occupancy on the server failed. Please try again later.',
            thinking: 'Thinking…', busy: 'The AI assistant is answering other questions. Please try again shortly.',
            no_answer: 'No answer from the AI. Please try again later.', interrupted: ' (connection lost)',
            connect_failed: 'Could not connect. Please try again later.',
            lang_switched: 'Switched to English. Answers are in English; questions work best in Chinese, '
                + 'and shelter names stay in Chinese.',
        },
    };

    function readLang() {
        try {
            const saved = localStorage.getItem(STORAGE_KEY);
            return saved === 'en' ? 'en' : 'zh';
        } catch {
            return 'zh';
        }
    }

    const I18N = {
        lang: readLang(),
        t(key, vars) {
            const table = DICT[this.lang] || DICT.zh;
            let text = key in table ? table[key] : (DICT.zh[key] ?? key);
            for (const [name, value] of Object.entries(vars || {})) {
                text = text.split(`{${name}}`).join(String(value));
            }
            return text;
        },
        apply() {
            document.documentElement.lang = this.lang === 'en' ? 'en' : 'zh-TW';
            document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = this.t(el.dataset.i18n); });
            document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
                el.placeholder = this.t(el.dataset.i18nPlaceholder);
            });
        },
        set(lang) {
            this.lang = lang === 'en' ? 'en' : 'zh';
            try { localStorage.setItem(STORAGE_KEY, this.lang); } catch { /* 無痕模式等情況：只是不記住 */ }
            this.apply();
        },
    };
    window.I18N = I18N;
    document.addEventListener('DOMContentLoaded', () => I18N.apply());
})();
