# Disaster Hub

**AI 驅動的台灣東部災害避難所決策系統**

以 2025 年花蓮光復鄉堰塞湖災害為出發點，針對現有避難所管理系統資訊不透明、無法模擬、決策緩慢三大痛點所開發的災害模擬決策系統。

---

## 功能特色

- **真實地圖視覺化** — Leaflet.js + OpenStreetMap，宜花東 50 處避難所標記於真實座標，marker 大小依容量縮放，點擊顯示即時資訊
- **PostGIS 空間模擬** — 設定災害中心點與影響半徑，後端透過 `ST_DWithin` 計算受影響避難所清單，支援強震、淹水、火災三種類型
- **人群疏散動畫** — 後端以鄉鎮人口模型估算圈內人口與疏散需求，人群點就近前往仍有空位的避難所，負載率即時變化；動畫結束後把各避難所收容人數回寫資料庫與向量索引，AI 助手回答的負載跟地圖一致
- **AI 決策助手** — 整合 RAG + 本地 LLM，根據避難所真實資料回答問題，支援語意查詢、地理距離查詢、容量排序查詢、模擬結果查詢

---

## 技術架構

```
資料層        PostgreSQL + PostGIS（空間資料）
              ChromaDB（語意向量索引）

後端層        FastAPI + Uvicorn
              ShelterRepository（資料庫操作，連線池）
              ChatService（意圖判斷 + RAG + LLM）
              VectorStore（ChromaDB 管理）

前端層        Leaflet.js + OpenStreetMap
              Canvas API（人群疏散動畫）
              iOS 風格 UI

容器化        Docker + Docker Compose
LLM           Ollama + llama3.2:3b（宿主機本地部署）
```

---

## 快速開始

### 環境需求

- Docker Desktop
- 宿主機安裝 [Ollama](https://ollama.com)（容器透過 `host.docker.internal` 連線）
- 16GB RAM 以上

### 1. 複製專案

```bash
git clone https://github.com/VesselST/disaster-hub.git
cd disaster-hub
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
| `CHROMA_PATH` | 向量索引落地路徑，Docker 由 compose 設為 `/data/chroma`；留空則索引只存在記憶體 |

可選變數：`OLLAMA_MODEL`（預設 `llama3.2:3b`）、`OLLAMA_TEMPERATURE`、`OLLAMA_NUM_PREDICT`、`OLLAMA_TIMEOUT`、`EMBEDDING_PROVIDER`（`ollama` 或 `minilm`）、`EMBEDDING_MODEL`（預設 `bge-m3`）、`EMBEDDING_TIMEOUT`、`RAG_TOP_K`、`DB_POOL_MIN` / `DB_POOL_MAX`。

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

啟動時若缺少必要環境變數（資料庫帳密等），服務會直接以清楚的錯誤訊息結束，不會帶著壞掉的設定跑起來。

### 開發模式（熱重載 + 測試依賴）

`docker-compose.yml` 是正式設定：不掛原始碼、不裝測試依賴、不開 `--reload`。要開發請疊上 `docker-compose.dev.yml`：

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

---

## 專案結構

```
Disaster_Hub/
├── app.py                      # FastAPI 主程式
├── config.py                   # 所有環境變數集中處
├── Dockerfile                  # INSTALL_DEV build arg 控制 dev 依賴
├── docker-compose.yml          # 正式設定
├── docker-compose.dev.yml      # 開發疊加（熱重載 / 原始碼掛載 / 測試依賴）
├── requirements.txt            # 執行期依賴
├── requirements-dev.txt        # 測試依賴（pytest / httpx）
├── init.sql                    # 資料庫初始化（首次啟動自動執行）
├── .env.example
├── .github/workflows/ci.yml    # push / PR 自動跑測試
├── conftest.py                 # 測試用環境變數預設值
│
├── models/
│   └── shelter.py              # Shelter 資料模型
│
├── repositories/
│   └── shelter_repository.py   # PostGIS 查詢（連線池、批次 upsert、schema migration）
│
├── services/
│   ├── data_fetcher.py         # 讀取 JSON 資料
│   ├── map_service.py          # 地圖資料格式化
│   ├── sync_service.py         # 資料同步
│   ├── chat_service.py         # 意圖判斷 + RAG + LLM
│   ├── shelter_profile.py      # 名稱/地址 → 鄉鎮、設施、別名
│   ├── embeddings.py           # Ollama / MiniLM embedding
│   ├── health.py               # readiness 依賴檢查
│   ├── population_service.py   # 海岸線 + 鄉鎮人口模型，疏散人數估算
│   └── vector_store.py         # ChromaDB 向量索引（支援局部更新）
│
├── data_for_refuge/
│   ├── hualien_shelter.json    # 花蓮（14 筆）
│   ├── taitung_shelter.json    # 台東（16 筆）
│   └── yilan_shelter.json      # 宜蘭（20 筆）
│
├── data_reference/
│   └── east_taiwan_population.json  # 海岸線折線 + 29 個鄉鎮人口（前端動畫與 AI 共用）
│
├── static/
│   ├── index.html              # 頁面骨架
│   ├── style.css               # 介面樣式
│   └── app.js                  # 地圖 / 模擬 / 聊天邏輯
│
└── tests/
    ├── test_shelter_model.py
    ├── test_map_service.py
    ├── test_data_fetcher2.py
    ├── test_shelter_profile.py
    ├── test_shelter_repository.py
    ├── test_population_service.py
    ├── test_vector_store.py
    ├── test_chat_service.py
    ├── test_eval_metrics.py
    ├── test_sync_service.py
    ├── test_config.py
    └── test_api_data.py
```

---

## API 端點

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/health` | liveness，只表示 process 還活著 |
| GET | `/health/ready` | readiness，實際檢查資料庫與向量索引；未就緒回 503 |
| GET | `/api/shelters` | 取得所有避難所資料 |
| GET | `/api/population` | 人口模型（海岸線折線 + 鄉鎮人口），前端人群動畫用 |
| POST | `/api/simulate_disaster` | 執行災害空間模擬，回傳受影響避難所與圈內人口 / 疏散需求 / 收容缺口估算 |
| POST | `/api/occupancy` | 回寫各避難所目前收容人數（疏散動畫結束後由前端呼叫），只重算有變動的向量文件 |
| POST | `/api/reset_simulation` | 清除模擬狀態，並把收容人數還原成來源資料的初始值 |
| POST | `/api/nearest_shelter` | 查詢最近避難所（PostGIS 距離排序）|
| POST | `/api/chat` | AI 決策助手 |
| POST | `/api/sync` | 手動觸發資料同步（需 `X-API-Key` header）|

`/health/ready` 把資料庫與向量索引視為必要條件（重啟容器可以恢復），Ollama 只回報狀態不影響判定（它跑在宿主機，重啟容器救不了）。容器的 `HEALTHCHECK` 打的是這支端點。

手動同步範例：

```bash
curl -X POST http://localhost:8501/api/sync -H "X-API-Key: $SYNC_API_KEY"
```

### 模擬狀態與收容人數

- 模擬流程：`/api/simulate_disaster` 用 PostGIS 找出受影響避難所，並以 `data_reference/east_taiwan_population.json` 的鄉鎮人口估算圈內人口、疏散需求與收容缺口；前端依同一份估算跑疏散動畫；動畫結束後把各避難所收容人數 `POST /api/occupancy` 回寫資料庫，並只對有變動的避難所重算向量文件。此後不論走 RAG、容量排序、地理距離或模擬快照，AI 看到的負載都與地圖一致。
- 啟動時與 `/api/sync` 的資料同步只更新容量、地址、座標，**不會**重設收容人數（容量縮到低於目前人數時才往下夾）。要清空收容人數請按「清除所有圖層」或呼叫 `/api/reset_simulation`。
- **已知限制：模擬狀態是全域的。** 目前的災害模擬結果與收容人數存在單一後端狀態與資料庫中，沒有 session 隔離；多位使用者同時操作會互相覆蓋對方的模擬。這是單人 demo 的設計取捨，要支援多人需要引入 session 或把模擬結果掛在使用者身上。

---

## 執行測試

測試全部使用 mock，不需要資料庫或 Ollama。push / PR 會由 GitHub Actions 自動執行。

```bash
docker exec -it disaster_app pytest tests/ -v
```

（容器內跑測試需要用開發模式啟動，正式 image 不含 pytest。）

或在本機：

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

## RAG 召回率評測

評測集 `evals/rag_eval.jsonl`（130 題，9 個類別）由 `evals/build_eval_set.py` 從 `data_for_refuge/` 產生，結構化類別（名稱、地區、鄉鎮、設施、容量）的正解由程式推導，別名／語意／負例為人工標註。

```bash
docker exec -it disaster_app python evals/build_eval_set.py
docker exec -it disaster_app python evals/run_rag_eval.py --embedder minilm --label baseline
docker exec -it disaster_app python evals/run_rag_eval.py --embedder ollama:bge-m3 --label bge-m3
```

輸出各類別的 Recall@3/5/10、Precision、MRR、hit rate、相關文件平均距離、負例的 top-1 距離（用來選相似度門檻）與查詢延遲 p50/p95；完整結果存在 `evals/results/<label>.json`。`--threshold` 可指定 cosine 距離門檻計算負例的假陽性率，`--doc-style legacy` 用舊的文件模板建索引以做對照。

歷次結果（macro Recall@10 / top-10 完全未命中題數）：

| 設定 | R@3 | R@10 | MRR | 未命中 |
|---|---|---|---|---|
| MiniLM + 舊文件（baseline） | 0.379 | 0.619 | 0.561 | 27 |
| MiniLM + 擴充文件 | 0.530 | 0.775 | 0.726 | 10 |
| bge-m3 + 舊文件 | 0.674 | 0.865 | 0.864 | 4 |
| **bge-m3 + 擴充文件（現行）** | **0.748** | **0.898** | **0.926** | **2** |

擴充文件指 `services/shelter_profile.py` 從名稱與地址推導出的鄉鎮、設施類型、容量分級、別名，寫進向量文件與 metadata。

```
Disaster_Hub/
├── evals/
│   ├── build_eval_set.py       # 產生評測集
│   ├── rag_eval.jsonl          # 評測集
│   ├── run_rag_eval.py         # 執行評測
│   ├── metrics.py              # Recall / Precision / MRR
│   └── results/                # 各次評測輸出
```

---

## AI 查詢範例

```
花蓮哪間避難所容量最大？
→ 直接排序資料庫回傳正確結果

緯度 23.99 經度 121.60 附近哪裡最近？
→ PostGIS ST_Distance 真實地理距離排序

哪些避難所受到影響？
→ 直接讀取模擬結果，不走 RAG

目前受影響的避難所還有空間嗎？
→ RAG 語意搜尋 + 模擬 context
```

---

## 開發者

**Vessel**
