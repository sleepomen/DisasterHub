# 安裝與開發

[← README](../../README.zh-TW.md) · [English](../setup.md) · 繁體中文

## 快速開始

### 環境需求

- Docker Desktop
- 宿主機安裝 [Ollama](https://ollama.com)（容器透過 `host.docker.internal` 連線）
- 16GB RAM 以上

### 1. 複製專案

```bash
git clone https://github.com/sleepomen/DisasterHub.git
cd DisasterHub
```

### 2. 設定環境變數

```bash
cp .env.example .env
```

編輯 `.env`：

| 變數 | 說明 |
|---|---|
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | 資料庫帳密，首次啟動時自動建立 |
| `POSTGRES_HOST` / `POSTGRES_PORT` | 容器內請保持 `disaster_db` / `5432` |
| `OLLAMA_HOST` | Ollama 位址，預設 `http://host.docker.internal:11434` |
| `SYNC_API_KEY` | 手動呼叫 `/api/sync` 所需的金鑰，請換成隨機長字串 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 管理者帳號。執行模擬、回寫收容人數、重置模擬前要先在網頁登入；未設定時這三個端點停用（503） |
| `SESSION_SECRET` | 簽署登入 cookie 的隨機長字串。未設定時啟動會隨機產生，服務重啟後所有人要重新登入 |
| `SESSION_HOURS` | 登入有效時數，預設 12 |
| `WRITE_API_KEY` | 選配。給 curl 或排程腳本用的寫入金鑰（`X-API-Key` header），留空即停用 |
| `CHROMA_PATH` | 向量索引落地路徑，Docker 由 compose 設為 `/data/chroma`；留空則索引只存在記憶體 |
| `TRUST_PROXY_HEADERS` | 前面有反向代理（Caddy / nginx）時才設 `true`：登入節流會改用 `X-Forwarded-For` 裡的真實來源 IP，走 HTTPS 時 cookie 會標記 `Secure`。直接對外時保持 `false`，否則任何人自己帶這個 header 就能繞過登入節流 |
| `APP_BIND` | 容器對外發布的宿主機位址，預設 `127.0.0.1`，只有同一台機器上的反向代理連得到。沒有代理、要讓區網其他機器直接連時改 `0.0.0.0` |
| `CHAT_MAX_CONCURRENT` | 同時進行的 LLM 生成上限，預設 2。超出的問題立刻回 429 而不是排隊，地圖與 readiness 才不會跟著 Ollama 一起等 |
| `CHAT_RATE_LIMIT` / `CHAT_RATE_WINDOW` | 單一來源在一段時間內最多能問幾題 `/api/chat`，預設 60 秒 15 題。併發上限只擋「同時幾個」，這個擋的是「同一個人總共能問幾次」——聊天端點不需要登入，不然任何人都能拿它當免費 LLM。設 `CHAT_RATE_LIMIT=0` 可停用 |
| `ALERT_POLL_SECONDS` / `ALERT_TTL_MINUTES` | 多久抓一次即時土石流警戒（預設 600 秒），以及之後抓取一直失敗時，已抓到的警戒維持多久（預設 180 分鐘）。`ALERT_POLL_SECONDS=0` 停用抓取，給沒有網路的環境用；測試一律設為 0 |
| `DB_CONNECT_TIMEOUT` / `DB_STATEMENT_TIMEOUT_MS` | 資料庫連線逾時（秒，預設 5）與單一 SQL 逾時（毫秒，預設 15000），資料庫卡住時請求會在幾秒內失敗，不會把所有工作執行緒一起卡死 |

可選變數：`OLLAMA_MODEL`（預設 `llama3.2:3b`）、`OLLAMA_TEMPERATURE`、`OLLAMA_NUM_CTX`（預設 8192，要放得下列舉題一次撈出的 20 至 30 筆避難所文件）、`OLLAMA_NUM_PREDICT`（預設 800）、`OLLAMA_TIMEOUT`、`EMBEDDING_PROVIDER`（`ollama` 或 `minilm`）、`EMBEDDING_MODEL`（預設 `bge-m3`）、`EMBEDDING_TIMEOUT`、`RAG_TOP_K`、`RAG_MAX_DISTANCE`（預設 0.70，純語意檢索的 cosine 距離超過此值就視為沒有相關資料；離題問題已經由前面幾層擋掉，這裡只是最後一道保險）、`KNOWLEDGE_MAX_DISTANCE`（預設 0.45，知識文件的同一種門檻）、`DB_POOL_MIN` / `DB_POOL_MAX`（預設 1 / 10）。

數值設定啟動時會檢查範圍（例如 `SESSION_HOURS` 必須大於 0、`DB_POOL_MAX` 不能小於 `DB_POOL_MIN`）；超出範圍會以清楚的訊息停止服務，解析不了的值會退回預設並在 log 提醒。

Leaflet 已打包在 `static/vendor/leaflet/`，沒有對外網路時地圖介面仍能載入；CARTO 底圖圖磚與 Google Fonts 仍需連線，離線時會退化成灰底與系統字型，不影響操作。

### 3. 下載模型（第一次需要，在宿主機執行）

```bash
ollama pull llama3.2:3b   # 聊天模型，可用 OLLAMA_MODEL 改成 qwen2.5:7b 等
ollama pull bge-m3        # 多語 embedding 模型，RAG 檢索用
```

### 4. 啟動服務

```bash
docker compose up --build
```

首次啟動會自動執行 `init.sql` 建表、同步 `data_for_refuge/` 的 JSON 到 PostGIS，並建立向量索引。開啟 <http://localhost:8501>。

向量索引會落在 `chroma_data` volume，之後重啟若資料沒變就直接沿用，不再重跑 embedding。要強制重建索引請呼叫 `/api/sync`。

啟動時若缺少必要環境變數（資料庫帳密等），服務會直接以清楚的錯誤訊息結束，不會帶著壞掉的設定跑起來。來源 JSON 讀不出來時也一樣：啟動同步與 `/api/sync` 會回報失敗，而不是默默帶著空資料繼續；缺名稱或座標不合法的單筆資料會被略過並在 log 計數。

容器內的應用程式以非特權的 `app` 使用者執行。entrypoint 啟動時會先把 `chroma_data` volume 的擁有者改過來，舊版（root 執行）映像建立的 volume 不必手動處理就能沿用。

### 開發模式（熱重載 + 測試依賴）

`docker-compose.yml` 是正式設定：不掛原始碼、不裝測試依賴、不開 `--reload`。要開發請疊上 `docker-compose.dev.yml`：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

## 執行測試

測試全部使用 mock，不需要資料庫或 Ollama。push / PR 會由 GitHub Actions 自動執行。

```bash
docker exec -it disaster_app pytest tests/ -v
```

（容器內跑測試需要用開發模式啟動，正式 image 不含 pytest。）

或在本機：

```bash
pip install --only-binary :all: -r requirements-dev.lock
pytest tests/ -v
```

從 lock 檔安裝才會拿到跟映像一樣的版本。注意 `chromadb` 0.5.x 依賴的 `chroma-hnswlib`
沒有 CPython 3.12 以上的 wheel，所以本機要用 Python 3.12 或更舊的版本。

Lint（用 CI 釘的同一版，兩邊結果才一致）：

```bash
pip install ruff==0.16.10
ruff check .
```

CI 有三個 job：`ruff check .`；在乾淨的 Python 3.12 上從 lock 檔安裝後跑測試，
接著把路由評測當回歸哨兵跑一次（`--min-accuracy 1.0`）；以及 `docker build` 之後在**映像裡面**
跑測試——最後這個才證明映像自己帶齊了依賴，而且「不需要編譯器」的前提還成立。

## 部署建議

- 一定要走 HTTPS，否則帳密與 cookie 會以明文經過網路。最簡單的方式是前面放一個會自動簽發憑證的反向代理，例如 Caddy：

  ```
  disasterhub.example.org {
      reverse_proxy 127.0.0.1:8501
  }
  ```

  設定 `TRUST_PROXY_HEADERS=true` 後，應用程式會依 `X-Forwarded-Proto` 判斷是否加上 cookie 的 `Secure` 屬性，並用 `X-Forwarded-For` 的真實來源做登入節流；Caddy 與 nginx 預設都會帶這兩個 header。沒有代理時請保持關閉。
- 容器預設只把 8501 發布在 `127.0.0.1`（`APP_BIND`），外部只能經由代理進來；8501 直接對外的話，任何人都能繞過 HTTPS 用明文登入。
- 若單位已有 SSO 或 VPN，可以把整個網域放在後面，應用程式內的帳密登入就成為第二層保護。
- `SESSION_SECRET` 請設成固定的隨機長字串，否則每次重啟都會把所有人登出。
- **已知限制：登出只在瀏覽器端生效。** session 是無狀態的簽章 cookie，登出只會清掉瀏覽器裡的 cookie，事前被複製走的 cookie 在到期（`SESSION_HOURS`）前仍然有效。共用機器請把有效時數設短，或換掉 `SESSION_SECRET` 讓所有 session 一次失效。
- **已知限制：模擬狀態是全域的。** 目前的災害模擬結果與收容人數存在單一後端狀態與資料庫中，沒有 session 隔離；多位使用者同時操作會互相覆蓋對方的模擬。這是單人 demo 的設計取捨，要支援多人需要引入 session 或把模擬結果掛在使用者身上。
