# Disaster Hub

**AI 驅動的台灣東部災害避難所決策系統**

以 2025 年花蓮光復鄉堰塞湖災害為出發點，針對現有避難所管理系統資訊不透明、無法模擬、決策緩慢三大痛點所開發的災害模擬決策系統。

---

## 功能特色

- **真實地圖視覺化** — Leaflet.js + OpenStreetMap，宜花東 50 處避難所標記於真實座標，marker 大小依容量縮放，點擊顯示即時資訊
- **PostGIS 空間模擬** — 設定災害中心點與影響半徑，後端透過 `ST_DWithin` 計算受影響避難所清單，支援強震、淹水、火災三種類型
- **人群疏散動畫** — 依災害類型與影響面積估算疏散人數，人群點就近前往仍有空位的避難所，負載率即時變化
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

可選變數：`OLLAMA_MODEL`（預設 `llama3.2:3b`）、`OLLAMA_TEMPERATURE`、`OLLAMA_NUM_PREDICT`、`OLLAMA_TIMEOUT`、`RAG_TOP_K`、`DB_POOL_MIN` / `DB_POOL_MAX`。

### 3. 下載 LLM 模型（第一次需要，在宿主機執行）

```bash
ollama pull llama3.2:3b
```

### 4. 啟動服務

```bash
docker compose up --build
```

首次啟動會自動執行 `init.sql` 建表、同步 `data_for_refuge/` 的 JSON 到 PostGIS，並建立向量索引。開啟 <http://localhost:8501>。

---

## 專案結構

```
Disaster_Hub/
├── app.py                      # FastAPI 主程式
├── config.py                   # 所有環境變數集中處
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── init.sql                    # 資料庫初始化（首次啟動自動執行）
├── .env.example
│
├── models/
│   └── shelter.py              # Shelter 資料模型
│
├── repositories/
│   └── shelter_repository.py   # PostGIS 查詢（連線池、批次 upsert）
│
├── services/
│   ├── data_fetcher.py         # 讀取 JSON 資料
│   ├── map_server.py           # 地圖資料格式化
│   ├── sync_service.py         # 資料同步
│   ├── chat_service.py         # 意圖判斷 + RAG + LLM
│   └── vector_store.py         # ChromaDB 向量索引
│
├── data_for_refuge/
│   ├── hualien_shelter.json    # 花蓮（14 筆）
│   ├── taitung_shelter.json    # 台東（16 筆）
│   └── yilan_shelter.json      # 宜蘭（20 筆）
│
├── static/
│   └── index.html              # 前端介面
│
└── tests/
    ├── test_shelter_model.py
    ├── test_map_service.py
    ├── test_data_fetcher2.py
    ├── test_chat_service.py
    ├── test_sync_service.py
    └── test_aSpi_data.py
```

---

## API 端點

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/3d_data` | 取得所有避難所資料 |
| POST | `/api/simulate_disaster` | 執行災害空間模擬 |
| POST | `/api/reset_simulation` | 清除模擬狀態 |
| POST | `/api/nearest_shelter` | 查詢最近避難所（PostGIS 距離排序）|
| POST | `/api/chat` | AI 決策助手 |
| POST | `/api/sync` | 手動觸發資料同步（需 `X-API-Key` header）|

手動同步範例：

```bash
curl -X POST http://localhost:8501/api/sync -H "X-API-Key: $SYNC_API_KEY"
```

---

## 執行測試

測試全部使用 mock，不需要資料庫或 Ollama：

```bash
docker exec -it disaster_app pytest tests/ -v
```

## RAG 召回率評測

評測集 `evals/rag_eval.jsonl`（130 題，9 個類別）由 `evals/build_eval_set.py` 從 `data_for_refuge/` 產生，結構化類別（名稱、地區、鄉鎮、設施、容量）的正解由程式推導，別名／語意／負例為人工標註。

```bash
docker exec -it disaster_app python evals/build_eval_set.py
docker exec -it disaster_app python evals/run_rag_eval.py --embedder minilm --label baseline
docker exec -it disaster_app python evals/run_rag_eval.py --embedder ollama:bge-m3 --label bge-m3
```

輸出各類別的 Recall@3/5/10、Precision、MRR、hit rate、相關文件平均距離、負例的 top-1 距離（用來選相似度門檻）與查詢延遲 p50/p95；完整結果存在 `evals/results/<label>.json`。`--threshold` 可指定 cosine 距離門檻計算負例的假陽性率。

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
