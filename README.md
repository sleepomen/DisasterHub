# Disaster Hub

<div align="center">

[![English](https://img.shields.io/badge/English-EN-1f6feb?style=for-the-badge)](#english)
[![繁體中文](https://img.shields.io/badge/%E7%B9%81%E9%AB%94%E4%B8%AD%E6%96%87-%E4%B8%AD%E6%96%87%E7%89%88-1f6feb?style=for-the-badge)](#zh-tw)

</div>

<a name="english"></a>


**An AI-powered shelter decision system for disasters in eastern Taiwan.**

Built in response to the 2025 barrier-lake disaster in Guangfu Township, Hualien, this simulation and decision system targets the three main pain points of existing shelter management: opaque information, no way to simulate, and slow decisions.

---

## Features

- **Real map visualization** — Leaflet.js + OpenStreetMap. 50 shelters across Yilan, Hualien and Taitung are plotted at their real coordinates; marker size scales with capacity, and clicking one shows live information.
- **PostGIS spatial simulation** — Set a disaster epicenter and impact radius; the backend computes the list of affected shelters via `ST_DWithin`. Earthquake, flood and fire scenarios are supported.
- **Crowd evacuation animation** — The backend estimates the population inside the impact radius and the resulting evacuation demand from a township population model. Crowd points head to the nearest shelter that still has room, and load ratios update in real time. When the animation ends, each shelter's occupancy is written back to the database and the vector index, so the loads the AI assistant reports match the map.
- **AI decision assistant** — A rules layer plus RAG and a local LLM answer questions from real shelter data, covering semantic lookups, geographic distance queries, capacity rankings, and simulation-result queries. Region, township, facility and capacity conditions become metadata filters, and questions about counties outside the coverage area are declined outright.

---

## Architecture

```
Data          PostgreSQL + PostGIS (spatial data)
              ChromaDB (semantic vector index)

Backend       FastAPI + Uvicorn
              ShelterRepository (database access, connection pool)
              ChatService (intent detection + RAG + LLM)
              VectorStore (ChromaDB management)

Frontend      Leaflet.js + OpenStreetMap
              Canvas API (crowd evacuation animation)
              iOS-style UI

Containers    Docker + Docker Compose
LLM           Ollama + llama3.2:3b (running locally on the host)
```

---

## Quick Start

### Requirements

- Docker Desktop
- [Ollama](https://ollama.com) installed on the host (containers reach it via `host.docker.internal`)
- 16 GB RAM or more

### 1. Clone the project

```bash
git clone https://github.com/VesselST/disaster-hub.git
cd disaster-hub
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:

| Variable | Description |
|---|---|
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | Database credentials, created automatically on first start |
| `POSTGRES_HOST` / `POSTGRES_PORT` | Keep `disaster_db` / `5432` inside containers |
| `OLLAMA_HOST` | Ollama address, defaults to `http://host.docker.internal:11434` |
| `SYNC_API_KEY` | Key required to call `/api/sync` manually — replace it with a long random string |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Administrator account. Running a simulation, writing back occupancy and resetting the simulation require logging in through the web UI first; when unset, those three endpoints are disabled (503) |
| `SESSION_SECRET` | Long random string used to sign the login cookie. When unset, one is generated at startup and every login is invalidated on restart |
| `SESSION_HOURS` | Login validity in hours, default 12 |
| `WRITE_API_KEY` | Optional. Write key for curl or scheduled scripts (`X-API-Key` header); leave empty to disable |
| `CHROMA_PATH` | Where the vector index is persisted; compose sets it to `/data/chroma` for Docker. Leave it empty and the index lives only in memory |
| `TRUST_PROXY_HEADERS` | Set to `true` only when a reverse proxy (Caddy / nginx) sits in front. Then the login throttle keys on the real client IP from `X-Forwarded-For` and the cookie is marked `Secure` behind HTTPS. Leave `false` when the app is exposed directly, otherwise anyone can bypass the throttle by sending the header themselves |
| `APP_BIND` | Host address the container port is published on, default `127.0.0.1` so only the reverse proxy on the same machine can reach it. Set `0.0.0.0` if there is no proxy and other machines on the LAN must connect directly |
| `CHAT_MAX_CONCURRENT` | How many LLM generations may run at once, default 2. Extra questions get an immediate 429 instead of queueing, so the map and readiness never wait on Ollama |
| `DB_CONNECT_TIMEOUT` / `DB_STATEMENT_TIMEOUT_MS` | Database connect timeout (seconds, default 5) and per-statement timeout (milliseconds, default 15000), so a hung database fails requests within seconds instead of blocking every worker thread |

Optional variables: `OLLAMA_MODEL` (default `llama3.2:3b`), `OLLAMA_TEMPERATURE`, `OLLAMA_NUM_CTX` (default 8192 — must hold the 20–30 shelter documents a list-style query retrieves), `OLLAMA_NUM_PREDICT` (default 800), `OLLAMA_TIMEOUT`, `EMBEDDING_PROVIDER` (`ollama` or `minilm`), `EMBEDDING_MODEL` (default `bge-m3`), `EMBEDDING_TIMEOUT`, `RAG_TOP_K`, `DB_POOL_MIN` / `DB_POOL_MAX` (default 1 / 10).

Numeric settings are range-checked at startup (for example `SESSION_HOURS` must be positive and `DB_POOL_MAX` may not be smaller than `DB_POOL_MIN`); an out-of-range value stops the service with a clear message, and a value that cannot be parsed falls back to the default with a warning in the log.

Leaflet is bundled under `static/vendor/leaflet/`, so the map UI loads without internet access; the CARTO basemap tiles and Google Fonts are still fetched online and degrade gracefully (grey tiles, system fonts) when offline.

### 3. Pull the models (first time only, on the host)

```bash
ollama pull llama3.2:3b   # chat model; switch to qwen2.5:7b etc. via OLLAMA_MODEL
ollama pull bge-m3        # multilingual embedding model used for RAG retrieval
```

### 4. Start the services

```bash
docker compose up --build
```

The first start runs `init.sql` to create the tables, syncs the JSON files in `data_for_refuge/` into PostGIS, and builds the vector index. Then open <http://localhost:8501>.

The vector index is stored in the `chroma_data` volume. Later restarts reuse it as long as the data has not changed, so embeddings are not recomputed. To force a rebuild, call `/api/sync`.

If required environment variables (database credentials and the like) are missing at startup, the service exits with a clear error message instead of running with a broken configuration. The same applies when the source JSON files cannot be read: startup sync and `/api/sync` report a failure instead of silently continuing with an empty dataset, and rows that lack a name or valid coordinates are skipped and counted in the log.

The application process runs as an unprivileged `app` user inside the container. The entrypoint fixes the ownership of the `chroma_data` volume on start, so a volume created by an older (root-running) image keeps working without manual steps.

### Development mode (hot reload + test dependencies)

`docker-compose.yml` is the production setup: no source mount, no test dependencies, no `--reload`. For development, layer `docker-compose.dev.yml` on top:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

---

## Project Structure

```
Disaster_Hub/
├── app.py                      # FastAPI application
├── config.py                   # Single place for all environment variables
├── Dockerfile                  # INSTALL_DEV build arg controls dev dependencies
├── docker-compose.yml          # Production setup
├── docker-compose.dev.yml      # Dev overlay (hot reload / source mount / test deps)
├── requirements.txt            # Runtime dependencies
├── requirements-dev.txt        # Test dependencies (pytest / httpx)
├── init.sql                    # Database bootstrap (runs automatically on first start)
├── .env.example
├── .github/workflows/ci.yml    # Runs the tests on push / PR
├── conftest.py                 # Default environment variables for tests
│
├── models/
│   └── shelter.py              # Shelter data model
│
├── repositories/
│   └── shelter_repository.py   # PostGIS queries (pooling, batch upsert, schema migration)
│
├── services/
│   ├── data_fetcher.py         # Reads the JSON data
│   ├── map_service.py          # Map data formatting
│   ├── sync_service.py         # Data synchronization
│   ├── chat_service.py         # Intent detection + RAG + LLM
│   ├── query_rules.py          # Query rules layer: region / township / facility / capacity → metadata filters, out-of-scope rejection
│   ├── shelter_profile.py      # Name/address → township, facilities, aliases
│   ├── embeddings.py           # Ollama / MiniLM embeddings
│   ├── health.py               # Readiness dependency checks
│   ├── population_service.py   # Coastline + township population model, evacuee estimation
│   └── vector_store.py         # ChromaDB vector index (supports partial updates)
│
├── data_for_refuge/
│   ├── hualien_shelter.json    # Hualien (14 entries)
│   ├── taitung_shelter.json    # Taitung (16 entries)
│   └── yilan_shelter.json      # Yilan (20 entries)
│
├── data_reference/
│   └── east_taiwan_population.json  # Coastline polylines + population of 29 townships (shared by the frontend animation and the AI)
│
├── static/
│   ├── index.html              # Page skeleton
│   ├── style.css               # UI styles
│   └── app.js                  # Map / simulation / chat logic
│
└── tests/
    ├── test_shelter_model.py
    ├── test_map_service.py
    ├── test_data_fetcher2.py
    ├── test_shelter_profile.py
    ├── test_shelter_repository.py
    ├── test_population_service.py
    ├── test_query_rules.py
    ├── test_query_rules.py
    ├── test_vector_store.py
    ├── test_chat_service.py
    ├── test_eval_metrics.py
    ├── test_sync_service.py
    ├── test_config.py
    └── test_api_data.py
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness — only reports that the process is alive |
| GET | `/health/ready` | Readiness — actually checks the database and vector index; returns 503 when not ready |
| GET | `/api/shelters` | Fetch all shelter data |
| GET | `/api/population` | The population model (coastline polylines + township population) used by the frontend crowd animation |
| POST | `/api/login` | Log in with the administrator credentials; sets an HttpOnly session cookie. Five consecutive failures lock the source for 5 minutes |
| POST | `/api/logout` | Log out and clear the cookie |
| GET | `/api/me` | Current login state; the frontend uses it on load to decide whether to show the login panel |
| POST | `/api/simulate_disaster` | Run the spatial disaster simulation; returns the affected shelters plus estimates of the population inside the radius, the evacuation demand, and the shelter shortfall (requires login, or `X-API-Key`) |
| POST | `/api/occupancy` | Write back each shelter's current occupancy (called by the frontend once the evacuation animation ends); only the changed vector documents are recomputed (requires login, or `X-API-Key`) |
| POST | `/api/reset_simulation` | Clear the simulation state and restore occupancy to the initial values from the source data (requires login, or `X-API-Key`) |
| POST | `/api/nearest_shelter` | Find the nearest shelters (PostGIS distance ordering) |
| POST | `/api/chat` | AI decision assistant. Returns 429 when all `CHAT_MAX_CONCURRENT` generation slots are busy |
| POST | `/api/sync` | Trigger a data sync manually (requires the `X-API-Key` header). The index is rebuilt into a staging collection and swapped in atomically, so chat keeps answering from the old index while embeddings are recomputed |

`/health/ready` treats the database and the vector index as hard requirements — restarting the container can recover them. Ollama is only reported, never decisive, because it runs on the host and restarting the container cannot fix it. The container `HEALTHCHECK` hits this endpoint.

Manual sync example:

```bash
curl -X POST http://localhost:8501/api/sync -H "X-API-Key: $SYNC_API_KEY"
```

### Simulation State and Occupancy

- The simulation flow: `/api/simulate_disaster` uses PostGIS to find the affected shelters and estimates the population inside the radius, the evacuation demand and the shelter shortfall from the township populations in `data_reference/east_taiwan_population.json`. The frontend runs the evacuation animation off that same estimate, and when the animation ends it writes each shelter's occupancy back to the database with `POST /api/occupancy`, recomputing vector documents only for the shelters that changed. From then on the loads the AI sees match the map, whether the answer comes from RAG, capacity ranking, geographic distance, or the simulation snapshot.
- The data sync at startup and via `/api/sync` only updates capacity, address and coordinates — it does **not** reset occupancy (it is only clamped down when the capacity shrinks below the current headcount). To clear occupancy, use "Clear all layers" in the UI or call `/api/reset_simulation`.
- After occupancy is written back, the snapshot's "current remaining space" is recomputed from the per-shelter figures, while "placeable / shortfall" stay as the planning numbers from the moment of simulation and the number of people already placed is reported separately, so the AI's totals never contradict its per-shelter list.
- Simulation, write-back and reset all mutate the database or global state, and once deployed anyone who can reach the domain can call these endpoints directly, so they require a login. Operators log in once when they open the site; the session cookie is sent automatically by the browser and nothing has to be typed again while it is valid. Without logging in the UI is read-only (map and chat).

### Deployment notes

- Always serve over HTTPS, otherwise credentials and cookies cross the network in plain text. The simplest option is a reverse proxy that issues certificates automatically, e.g. Caddy:

  ```
  disasterhub.example.org {
      reverse_proxy 127.0.0.1:8501
  }
  ```

  With `TRUST_PROXY_HEADERS=true` the app reads `X-Forwarded-Proto` to decide whether to mark the cookie `Secure` and `X-Forwarded-For` to key the login throttle on the real client; Caddy and nginx send both headers by default. Keep the flag off when there is no proxy.
- The container publishes port 8501 on `127.0.0.1` by default (`APP_BIND`), so the only way in from outside is through the proxy — exposing 8501 directly would let people bypass HTTPS and log in over plain HTTP.
- If your organisation already has SSO or a VPN, put the whole domain behind it and treat the in-app login as a second layer.
- Set `SESSION_SECRET` to a fixed long random string, otherwise every restart logs everyone out.
- **Known limitation: logout is client-side only.** Sessions are stateless signed cookies, so logging out clears the browser cookie but a copy stolen beforehand stays valid until it expires (`SESSION_HOURS`). Keep the lifetime short on shared machines, or rotate `SESSION_SECRET` to invalidate every session at once.
- **Known limitation: the simulation state is global.** The disaster simulation result and the occupancy live in a single backend state plus the database, with no session isolation, so concurrent users overwrite each other's simulations. That is a deliberate trade-off for a single-user demo; supporting multiple users would require sessions, or attaching the simulation result to the user.

---

## Running the Tests

All tests use mocks, so neither the database nor Ollama is needed. GitHub Actions runs them on every push and pull request.

```bash
docker exec -it disaster_app pytest tests/ -v
```

(Running the tests inside the container requires development mode — the production image does not include pytest.)

Or locally:

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

## RAG Recall Evaluation

The evaluation set `evals/rag_eval.jsonl` (130 questions across 9 categories) is generated from `data_for_refuge/` by `evals/build_eval_set.py`. Ground truth for the structured categories (name, region, township, facilities, capacity) is derived programmatically; alias, semantic and negative cases are labeled by hand.

```bash
docker exec -it disaster_app python evals/build_eval_set.py
docker exec -it disaster_app python evals/run_rag_eval.py --embedder minilm --label baseline
docker exec -it disaster_app python evals/run_rag_eval.py --embedder ollama:bge-m3 --label bge-m3
```

The run reports per-category Recall@3/5/10, Precision, MRR, hit rate, mean distance of the relevant documents, top-1 distance for the negative cases (used to pick a similarity threshold), and query latency p50/p95. Full results land in `evals/results/<label>.json`. `--threshold` sets a cosine distance cutoff for computing the false-positive rate on negatives, and `--doc-style legacy` builds the index with the old document template for comparison.

Results so far (macro; R@all is scored over the full returned list, "misses" counts questions with no relevant document in the top 10):

| Configuration | R@3 | R@10 | R@all | P@all | MRR | Misses | Negatives rejected |
|---|---|---|---|---|---|---|---|
| MiniLM + legacy documents (baseline) | 0.379 | 0.619 | 0.619 | – | 0.561 | 27 | 0/10 |
| MiniLM + enriched documents | 0.530 | 0.775 | 0.775 | – | 0.726 | 10 | 0/10 |
| bge-m3 + legacy documents | 0.674 | 0.865 | 0.865 | – | 0.864 | 4 | 0/10 |
| bge-m3 + enriched documents | 0.748 | 0.898 | 0.898 | 0.335 | 0.926 | 2 | 0/10 |
| **bge-m3 + enriched documents + rules layer (current)** | **0.794** | **0.936** | **0.997** | **0.730** | **0.990** | **0** | **8/10** |

Without the rules layer retrieval always returns the top 10, so R@all equals R@10. With it, the region, township, facility and capacity categories all reach R@all and P@all of 1.0 ("Which shelters are in Yilan?" returns all 20 with no noise). The only remaining partial miss is the bare street-name query "四維路", which is left for BM25 hybrid retrieval. The two negatives that are not rejected ("What's the weather today?" and "How do I apply for disaster relief?") are not geographic questions; the prompt rules answer them with "no relevant data".

Pass `--no-rules` to run the comparison without the rules layer:

```bash
docker exec -it disaster_app python evals/run_rag_eval.py --embedder ollama:bge-m3 --label bge-m3_rules
docker exec -it disaster_app python evals/run_rag_eval.py --embedder ollama:bge-m3 --label bge-m3 --no-rules
```

"Enriched documents" means the township, facility types, capacity tier and aliases that `services/shelter_profile.py` derives from the name and address, written into both the vector document and its metadata. The "rules layer" is `services/query_rules.py`: before vector retrieval it extracts the region (Yilan / Hualien / Taitung), township (29 of them, taken from the population model, including suffix-less forms such as 礁溪), facility type (school / elementary school / gymnasium / township office…) and capacity conditions (over a thousand, more than 500, 300 or fewer, small…), turns them into a ChromaDB `where` filter and returns every matching document; "largest / most" questions are re-ordered by capacity; an empty filtered result falls back to plain semantic retrieval. A question that names a county outside the coverage area (Taipei, Kaohsiung…) with no eastern place name in it is declined by the rules layer and never reaches the vector index.

```
Disaster_Hub/
├── evals/
│   ├── build_eval_set.py       # Generates the evaluation set
│   ├── rag_eval.jsonl          # The evaluation set
│   ├── run_rag_eval.py         # Runs the evaluation
│   ├── metrics.py              # Recall / Precision / MRR
│   └── results/                # Output of each run
```

---

## Example AI Queries

```
Which shelter in Hualien has the largest capacity?
→ Sorts the database directly and returns the correct answer

Which shelter is closest to latitude 23.99, longitude 121.60?
→ Real geographic distance ordering via PostGIS ST_Distance

Which shelters are affected?
→ Reads the simulation result directly, bypassing RAG

Do the affected shelters still have room?
→ Reads the simulation result directly while a simulation is active; falls back to RAG otherwise

Which shelters are in Yilan? / Elementary schools in Hualien / Places in Taitung that can take a thousand people
→ The rules layer turns the question into metadata filters and returns every matching shelter

Shelters in Kaohsiung?
→ Declined by the rules layer: the system only covers Yilan, Hualien and Taitung

Give me evacuation advice
→ With a simulation active, answers from the affected shelters and population estimate; otherwise asks you to run a simulation first
```

---

## Author

**Vessel**

<p align="right"><a href="#zh-tw">繁體中文 ↓</a> · <a href="#disaster-hub">Back to top ↑</a></p>

---
---

<a name="zh-tw"></a>

<div align="center">

[![English](https://img.shields.io/badge/English-EN-1f6feb?style=for-the-badge)](#english)
[![繁體中文](https://img.shields.io/badge/%E7%B9%81%E9%AB%94%E4%B8%AD%E6%96%87-%E4%B8%AD%E6%96%87%E7%89%88-1f6feb?style=for-the-badge)](#zh-tw)

</div>


**AI 驅動的台灣東部災害避難所決策系統**

以 2025 年花蓮光復鄉堰塞湖災害為出發點，針對現有避難所管理系統資訊不透明、無法模擬、決策緩慢三大痛點所開發的災害模擬決策系統。

---

## 功能特色

- **真實地圖視覺化** — Leaflet.js + OpenStreetMap，宜花東 50 處避難所標記於真實座標，marker 大小依容量縮放，點擊顯示即時資訊
- **PostGIS 空間模擬** — 設定災害中心點與影響半徑，後端透過 `ST_DWithin` 計算受影響避難所清單，支援強震、淹水、火災三種類型
- **人群疏散動畫** — 後端以鄉鎮人口模型估算圈內人口與疏散需求，人群點就近前往仍有空位的避難所，負載率即時變化；動畫結束後把各避難所收容人數回寫資料庫與向量索引，AI 助手回答的負載跟地圖一致
- **AI 決策助手** — 整合規則層 + RAG + 本地 LLM，根據避難所真實資料回答問題，支援語意查詢、地理距離查詢、容量排序查詢、模擬結果查詢；地區 / 鄉鎮 / 設施 / 容量條件轉成 metadata 篩選，範圍外縣市直接拒答

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
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 管理者帳號。執行模擬、回寫收容人數、重置模擬前要先在網頁登入；未設定時這三個端點停用（503） |
| `SESSION_SECRET` | 簽署登入 cookie 的隨機長字串。未設定時啟動會隨機產生，服務重啟後所有人要重新登入 |
| `SESSION_HOURS` | 登入有效時數，預設 12 |
| `WRITE_API_KEY` | 選配。給 curl 或排程腳本用的寫入金鑰（`X-API-Key` header），留空即停用 |
| `CHROMA_PATH` | 向量索引落地路徑，Docker 由 compose 設為 `/data/chroma`；留空則索引只存在記憶體 |
| `TRUST_PROXY_HEADERS` | 前面有反向代理（Caddy / nginx）時才設 `true`：登入節流會改用 `X-Forwarded-For` 裡的真實來源 IP，走 HTTPS 時 cookie 會標記 `Secure`。直接對外時保持 `false`，否則任何人自己帶這個 header 就能繞過登入節流 |
| `APP_BIND` | 容器對外發布的宿主機位址，預設 `127.0.0.1`，只有同一台機器上的反向代理連得到。沒有代理、要讓區網其他機器直接連時改 `0.0.0.0` |
| `CHAT_MAX_CONCURRENT` | 同時進行的 LLM 生成上限，預設 2。超出的問題立刻回 429 而不是排隊，地圖與 readiness 才不會跟著 Ollama 一起等 |
| `DB_CONNECT_TIMEOUT` / `DB_STATEMENT_TIMEOUT_MS` | 資料庫連線逾時（秒，預設 5）與單一 SQL 逾時（毫秒，預設 15000），資料庫卡住時請求會在幾秒內失敗，不會把所有工作執行緒一起卡死 |

可選變數：`OLLAMA_MODEL`（預設 `llama3.2:3b`）、`OLLAMA_TEMPERATURE`、`OLLAMA_NUM_CTX`（預設 8192，要放得下列舉題一次撈出的 20 至 30 筆避難所文件）、`OLLAMA_NUM_PREDICT`（預設 800）、`OLLAMA_TIMEOUT`、`EMBEDDING_PROVIDER`（`ollama` 或 `minilm`）、`EMBEDDING_MODEL`（預設 `bge-m3`）、`EMBEDDING_TIMEOUT`、`RAG_TOP_K`、`DB_POOL_MIN` / `DB_POOL_MAX`（預設 1 / 10）。

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
│   ├── query_rules.py          # 查詢規則層：地區 / 鄉鎮 / 設施 / 容量 → metadata 篩選，範圍外拒答
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
    ├── test_query_rules.py
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
| POST | `/api/login` | 以管理者帳密登入，成功後發 HttpOnly session cookie；連續失敗 5 次會鎖 5 分鐘 |
| POST | `/api/logout` | 登出，清除 cookie |
| GET | `/api/me` | 目前登入狀態，前端載入時用來決定是否顯示登入面板 |
| POST | `/api/simulate_disaster` | 執行災害空間模擬，回傳受影響避難所與圈內人口 / 疏散需求 / 收容缺口估算（需登入，或帶 `X-API-Key`）|
| POST | `/api/occupancy` | 回寫各避難所目前收容人數（疏散動畫結束後由前端呼叫），只重算有變動的向量文件（需登入，或帶 `X-API-Key`）|
| POST | `/api/reset_simulation` | 清除模擬狀態，並把收容人數還原成來源資料的初始值（需登入，或帶 `X-API-Key`）|
| POST | `/api/nearest_shelter` | 查詢最近避難所（PostGIS 距離排序）|
| POST | `/api/chat` | AI 決策助手。`CHAT_MAX_CONCURRENT` 個生成名額都在忙時回 429 |
| POST | `/api/sync` | 手動觸發資料同步（需 `X-API-Key` header）。索引會先建到暫存 collection 再整個換過去，重算 embedding 期間問答仍用舊索引回答 |

`/health/ready` 把資料庫與向量索引視為必要條件（重啟容器可以恢復），Ollama 只回報狀態不影響判定（它跑在宿主機，重啟容器救不了）。容器的 `HEALTHCHECK` 打的是這支端點。

手動同步範例：

```bash
curl -X POST http://localhost:8501/api/sync -H "X-API-Key: $SYNC_API_KEY"
```

### 模擬狀態與收容人數

- 模擬流程：`/api/simulate_disaster` 用 PostGIS 找出受影響避難所，並以 `data_reference/east_taiwan_population.json` 的鄉鎮人口估算圈內人口、疏散需求與收容缺口；前端依同一份估算跑疏散動畫；動畫結束後把各避難所收容人數 `POST /api/occupancy` 回寫資料庫，並只對有變動的避難所重算向量文件。此後不論走 RAG、容量排序、地理距離或模擬快照，AI 看到的負載都與地圖一致。
- 啟動時與 `/api/sync` 的資料同步只更新容量、地址、座標，**不會**重設收容人數（容量縮到低於目前人數時才往下夾）。要清空收容人數請按「清除所有圖層」或呼叫 `/api/reset_simulation`。
- 回寫收容人數後，模擬快照裡的「目前剩餘空間合計」會跟著逐筆避難所重算；「可安置 / 收容缺口」則保留為模擬當下的規劃數字，並另外標示已安置人數，AI 的總計與明細才不會互相矛盾。
- 模擬、回寫與重置都會改資料庫或全域狀態，部署出去之後任何連得到網域的人都能直接呼叫這些端點，因此需要先登入。操作員開網站時登入一次，session cookie 由瀏覽器自動帶，有效期內不必再輸入任何東西；不登入仍可瀏覽地圖與使用問答。

### 部署建議

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

歷次結果（macro；R@all 以整份回傳清單計分，未命中為 top-10 完全沒有相關文件的題數）：

| 設定 | R@3 | R@10 | R@all | P@all | MRR | 未命中 | 負例拒答 |
|---|---|---|---|---|---|---|---|
| MiniLM + 舊文件（baseline） | 0.379 | 0.619 | 0.619 | – | 0.561 | 27 | 0/10 |
| MiniLM + 擴充文件 | 0.530 | 0.775 | 0.775 | – | 0.726 | 10 | 0/10 |
| bge-m3 + 舊文件 | 0.674 | 0.865 | 0.865 | – | 0.864 | 4 | 0/10 |
| bge-m3 + 擴充文件 | 0.748 | 0.898 | 0.898 | 0.335 | 0.926 | 2 | 0/10 |
| **bge-m3 + 擴充文件 + 規則層（現行）** | **0.794** | **0.936** | **0.997** | **0.730** | **0.990** | **0** | **8/10** |

沒有規則層時檢索固定回傳 top-10，R@all 等於 R@10。加上規則層後，地區 / 鄉鎮 / 設施 / 容量四類的 R@all 與 P@all 都到 1.0（「宜蘭有哪些避難所」回傳全部 20 筆且沒有雜訊），剩下唯一的部分未命中是純路名查詢「四維路」，留給 BM25 hybrid。兩題沒被拒答的負例是「今天天氣如何」與「如何申請災害補助」，不是地理問題，交給 prompt 規則回「沒有相關資料」。

`--no-rules` 可以關掉規則層做對照：

```bash
docker exec -it disaster_app python evals/run_rag_eval.py --embedder ollama:bge-m3 --label bge-m3_rules
docker exec -it disaster_app python evals/run_rag_eval.py --embedder ollama:bge-m3 --label bge-m3 --no-rules
```

擴充文件指 `services/shelter_profile.py` 從名稱與地址推導出的鄉鎮、設施類型、容量分級、別名，寫進向量文件與 metadata。規則層指 `services/query_rules.py`：在向量檢索前抽出地區（宜蘭 / 花蓮 / 台東）、鄉鎮（29 個，來自人口模型，支援「礁溪」這種省略字尾的寫法）、設施類型（學校 / 國小 / 體育館 / 公所…）與容量條件（上千人、超過 500 人、300 人以下、小型…），轉成 ChromaDB `where` 篩選並回傳全部符合的文件；「最大 / 最多」再依容量重排；篩選結果為空時退回一般語意檢索。提到台北、高雄等範圍外縣市、且整句沒有東部地名時，在規則層直接拒答，不進向量檢索。

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
→ 模擬進行中時直接讀取模擬結果；沒有模擬時走 RAG

宜蘭有哪些避難所？／花蓮的國小／台東能收上千人的地方
→ 規則層轉成 metadata 篩選，回傳全部符合的避難所

高雄的避難所？
→ 規則層直接拒答：本系統只涵蓋宜蘭、花蓮、台東

請給我疏散建議
→ 有模擬時依受影響避難所與人口估算回答；沒有模擬時提示先執行模擬
```

---

## 開發者

**Vessel**

<p align="right"><a href="#english">English ↑</a> · <a href="#disaster-hub">回到頂端 ↑</a></p>
