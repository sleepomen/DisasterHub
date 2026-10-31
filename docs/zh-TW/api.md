# API 與監控

[← README](../../README.zh-TW.md) · [English](../api.md) · 繁體中文

## API 端點

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/health` | liveness，只表示 process 還活著 |
| GET | `/health/ready` | readiness，實際檢查資料庫與向量索引；未就緒回 503 |
| GET | `/api/shelters` | 取得所有避難所資料 |
| GET | `/api/population` | 人口模型（海岸線折線 + 鄉鎮人口），前端人群動畫用 |
| GET | `/api/stats` | 線上計數器與延遲百分位（需登入，或帶 `X-API-Key`）|
| POST | `/api/login` | 以管理者帳密登入，成功後發 HttpOnly session cookie；連續失敗 5 次會鎖 5 分鐘 |
| POST | `/api/logout` | 登出，清除 cookie |
| GET | `/api/me` | 目前登入狀態，前端載入時用來決定是否顯示登入面板 |
| POST | `/api/simulate_disaster` | 執行災害空間模擬，回傳受影響避難所、圈內人口 / 疏散需求 / 收容缺口估算、疏散分配（`evacuation`，每一筆附道路路線）與分配後的收容人數（已寫入資料庫）。`mode` 是 `walk`（預設）或 `drive`（需登入，或帶 `X-API-Key`）|
| POST | `/api/occupancy` | 直接設定各避難所目前收容人數（給外部工具或手動修正用，模擬已不需要呼叫），只重算有變動的向量文件（需登入，或帶 `X-API-Key`）|
| POST | `/api/reset_simulation` | 清除模擬狀態，並把收容人數還原成來源資料的初始值（需登入，或帶 `X-API-Key`）|
| POST | `/api/nearest_shelter` | 查詢最近避難所，依通行時間排序（`mode`：`walk` 或 `drive`）。每筆附 `road_distance_km`（算不出路線時是 `null`）、直線距離 `distance_km`、`walk_min`、`drive_min`、`hazard_m`（仍經過警戒範圍的公尺數）與 `unreachable`（被紅色警戒封路擋住）|
| GET | `/api/hazard_zones` | 土石流潛勢溪流影響範圍（GeoJSON），地圖圖層用 |
| GET | `/api/alerts` | 目前生效的土石流警戒，以及各自封閉或要繞行的道路長度 |
| POST | `/api/chat` | AI 決策助手，回答以 Server-Sent Events 逐段串流。`CHAT_MAX_CONCURRENT` 個生成名額都在忙時回 429 |
| POST | `/api/sync` | 手動觸發資料同步。`SYNC_API_KEY`、登入的 session、`WRITE_API_KEY` 三者任一即可——啟動同步是會失敗的，操作員要能不重啟容器就把資料救回來。索引會先建到暫存 collection 再整個換過去，重算 embedding 期間問答仍用舊索引回答 |

`/health/ready` 把資料庫與向量索引視為必要條件（重啟容器可以恢復），Ollama 只回報狀態不影響判定（它跑在宿主機，重啟容器救不了）。容器的 `HEALTHCHECK` 打的是這支端點。Ollama 變慢時 readiness 也仍然答得出來：查詢與文件的 embedding 都在索引鎖外面算，卡住的 embedding 不會擋住索引檢查用的 `count()`。啟動時的同步失敗不會讓服務起不來，而且會在背景依 5、15、30、60、120、300 秒退避重試直到成功——資料庫還沒起來、Ollama 還在載 embedding 模型都會在這段時間內自己好；在那之前 readiness 會持續回 503。

手動同步範例：

```bash
curl -X POST http://localhost:8501/api/sync -H "X-API-Key: $SYNC_API_KEY"
```

## 聊天回答的串流

`/api/chat` 回的是 `text/event-stream`，不是一包 JSON。列舉一個縣的避難所，模型要吐將近一分鐘（`region` 類別 p50 在 llama3.2:3b 約 60 秒、qwen2.5:7b 約 80 秒），讓使用者對著轉圈等這麼久跟當掉沒有區別。每個事件是一行 `data:` 加一個 JSON 物件：

```
data: {"type": "delta", "text": "甲避難所：容量 100 人"}
data: {"type": "delta", "text": "、乙避難所：容量 300 人"}
data: {"type": "done"}
```

- `delta`：答案的片段，收到就往後接。檢索層自己就答得出來的情況（範圍外、請提供座標、沒有相關資料）會以單一個 `delta` 回來，完全不會呼叫 LLM。
- `error`：給使用者看的訊息。前面已經送過 `delta` 代表串流中途斷了，畫面上的文字會留著並在後面補一句說明；如果它是第一個事件，表示生成從頭到尾沒有產出。
- `done`：串流結束。

生成名額會撐到整段串流結束（包含使用者中途關掉頁面）才歸還，所以 `CHAT_MAX_CONCURRENT` 仍然管得住同時生成的數量；單次生成也仍然最長 `OLLAMA_TIMEOUT` 秒，改由串流迴圈裡的 deadline 執行——串流模式下 requests 的 timeout 只管兩個片段之間的間隔，管不到整段時間。

```bash
curl -N -X POST http://localhost:8501/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "宜蘭有哪些避難所"}'
```

前面有反向代理時要關掉回應緩衝，否則串流等於沒做：這支端點會送 `X-Accel-Buffering: no` 給 nginx 看，Caddy 預設不緩衝。

## 線上計數器與日誌

`evals/` 下的評測量的是「模型在固定的 130 道題上表現如何」；這些計數器量的是「真實流量實際怎麼走」。
兩者不會自動一致，而沒有這些數字就無法判斷使用者問的問題跟當初調校用的題目像不像。

`GET /api/stats`（權限跟寫入端點一樣——它會揭露流量模式與節流狀態，所以不對外公開，
對外探測請用 `/health/ready`）回傳：

```json
{
  "uptime_s": 167.0,
  "counters": {
    "chat.route.rag": 1, "chat.route.out_of_scope": 1, "chat.route.needs_coords": 0,
    "chat.answer.early_reply": 1, "chat.answer.generated": 1,
    "chat.generation.ok": 1, "chat.generation.interrupted": 0, "chat.generation.unavailable": 0,
    "retrieval.named": 1, "retrieval.filtered": 0, "retrieval.fell_back": 0,
    "retrieval.no_match": 0, "retrieval.truncated": 0,
    "chat.rejected.rate_limit": 0, "chat.rejected.busy": 0
  },
  "latency_ms": {
    "chat.early_reply": {"count": 1, "p50": 3.1, "p95": 3.1, "max": 3.1},
    "chat.generated": {"count": 1, "p50": 12163.8, "p95": 12163.8, "max": 12163.8}
  }
}
```

計數器名稱是預先登記的，所以還沒發生過的指標仍然會以 `0` 出現——否則「從未發生」跟「沒有這個指標」
長得一樣，而這些數字的意義本來就在比例。值得盯的幾個：

- **`retrieval.fell_back` 除以 `chat.route.rag`** — 規則層抽出的條件在索引裡沒有東西符合、
  只好把「最接近的其他資料」送給模型的比例。這個比例上升代表 `services/chat/query_rules.py`
  的關鍵字表讀錯了真實問句。
- **`retrieval.no_match`** — 距離門檻拒答的次數。`RAG_MAX_DISTANCE` 是在評測集上調出來的，
  這是唯一能判斷它對真實問句是否同樣適用的訊號。
- **`retrieval.truncated`** — 命中 `MAX_FILTERED_RESULTS` 的次數，代表 prompt 看到的避難所
  比實際存在的少。目前資料量下恆為 0，等單一縣超過 30 間才會開始跳。
- **`chat.rejected.rate_limit` 對 `chat.rejected.busy`** — 有人在刷，還是生成名額不夠。
  兩者都回 429，但處理方式完全相反。
- **`chat.generation.interrupted`** — 答案吐到一半斷掉（上游掛了，或 `OLLAMA_TIMEOUT` 的
  deadline 觸發）。失敗的生成同樣會進延遲取樣，所以逾時不會悄悄把 p95 修漂亮。

全部只放記憶體、重啟歸零；百分位涵蓋的是每條路徑最近 512 筆樣本，不是開機以來全部。

每個回應都帶 `X-Request-ID`，每一行 log 都以它開頭，而每次問答會留下一行 `key=value` 摘要——
使用者回報「我剛才拿到的答案是錯的」時，直接拿那個 id 去 grep：

```
2026-10-05 08:59:12 INFO services.chat.service [e943c1f1]: chat route=rag source=generated outcome=ok context_chars=138 reply_chars=34 ms=12164
```

只有 `TRUST_PROXY_HEADERS=true` 時才會沿用上游帶進來的 `X-Request-ID`，而且必須符合
`[A-Za-z0-9._-]{1,64}`：這個值會進 log，不檢查等於讓任何人把換行塞進日誌。
