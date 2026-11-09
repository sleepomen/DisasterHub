// AI 助手的對話框與串流回答
// 所有檔案用一般的 <script> 依序載入、共用同一個全域範圍（見 index.html 的順序）；
// 頂層只呼叫同一個檔案或前面檔案定義的函式，其餘都在事件發生時才呼叫

function addChat(m, t) {
    const c = document.getElementById('chat-container');
    const d = document.createElement('div');
    d.className = `msg msg-${t}`;
    d.textContent = m;
    c.appendChild(d);
    c.scrollTop = c.scrollHeight;
    return d;
}

// 一次只送一個問題：等回答期間鎖住輸入框，連按 Enter 不會對後端塞進多個生成請求
let chatPending = false;

function setChatBusy(busy) {
    chatPending = busy;
    const input = document.getElementById('user-input');
    const btn = document.querySelector('.send-btn');
    if (input) input.disabled = busy;
    if (btn) btn.disabled = busy;
    if (!busy && input) input.focus();
}

// 讀後端的 SSE：每個事件是一行 data: {json}，事件之間以空白行分隔。
// 跨 chunk 的半個事件要留在 buffer 裡等下一段，不能直接丟掉
async function* readChatEvents(body) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let cut;
        while ((cut = buffer.indexOf('\n\n')) !== -1) {
            const frame = buffer.slice(0, cut);
            buffer = buffer.slice(cut + 2);
            for (const line of frame.split('\n')) {
                if (!line.startsWith('data:')) continue;
                try {
                    yield JSON.parse(line.slice(5));
                } catch {
                    console.error('無法解析的 SSE 事件', line);
                }
            }
        }
    }
}

// 列舉題生成要一分鐘以上，所以邊收邊貼：第一個字到了就把「思考中」換成答案泡泡
window.sendChat = async function(v) {
    v = (v || '').trim();
    if (!v || chatPending) return;
    setChatBusy(true);
    addChat(v, 'user');
    document.getElementById('user-input').value = '';
    const container = document.getElementById('chat-container');
    const loadingEl = addChat(I18N.t('thinking'), 'loading');
    let bubble = null;
    try {
        const res = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: v, lang: I18N.lang })
        });
        if (res.status === 429) {
            // 後端的生成名額滿了（別人正在問），不是連線問題
            const data = await res.json().catch(() => ({}));
            addChat(typeof data.detail === 'string' ? data.detail : I18N.t('busy'), 'ai');
            return;
        }
        if (!res.ok || !res.body) throw new Error('HTTP ' + res.status);
        for await (const event of readChatEvents(res.body)) {
            if (event.type === 'done') break;
            if (!event.text) continue;
            if (!bubble) {
                loadingEl.remove();
                bubble = addChat('', 'ai');
            }
            bubble.textContent += event.text;
            container.scrollTop = container.scrollHeight;
        }
        if (!bubble) addChat(I18N.t('no_answer'), 'ai');
    } catch {
        // 串流中途斷線：已經收到的文字留在畫面上，後面補一句說明
        if (bubble) bubble.textContent += I18N.t('interrupted');
        else addChat(I18N.t('connect_failed'), 'ai');
    } finally {
        loadingEl.remove();
        setChatBusy(false);
    }
};
