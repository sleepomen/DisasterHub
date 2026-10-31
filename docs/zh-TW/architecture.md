# 架構

[← README](../../README.zh-TW.md) · [English](../architecture.md) · 繁體中文

<p align="center"><img src="../images/map.jpg" alt="644 間官方避難所地圖" width="100%"></p>

## 功能特色

- **真實地圖視覺化** — Leaflet.js + OpenStreetMap，宜花東 644 處官方避難所（消防署開放資料）標記於真實座標，marker 大小依容量縮放，點擊顯示即時資訊
- **PostGIS 空間模擬** — 設定災害中心點與影響半徑，後端透過 `ST_DWithin` 計算受影響避難所清單，支援強震、淹水、火災三種類型
- **道路路線規劃** — 最近避難所與疏散分配都沿 OpenStreetMap 路網（pgRouting）計算，不是直線：步行或開車最快到的排前面，實際路線畫在地圖上，動畫裡的人群也沿著路線移動
- **人群疏散動畫** — 後端以鄉鎮人口模型估算圈內人口與疏散需求，算出確定、不超過容量的疏散分配（近者優先），並立即寫入資料庫與向量索引；前端只負責把這份分配畫成動畫，模擬一回傳，AI 助手回答的負載就跟地圖一致
- **土石流潛勢區域** — 匯入農業部宜花東 531 個土石流潛勢溪流影響範圍到 PostGIS 並顯示在地圖上；淹水模擬不把人送進範圍內的避難所，查最近避難所時若落在範圍內會加註警告
- **避開災害範圍與即時警戒** — 淹水時路線繞開土石流潛勢範圍。每 10 分鐘抓一次農村水保署的土石流警戒：紅色警戒範圍內的道路封閉、黃色警戒範圍內的道路盡量繞行；沒有安全道路可到的避難所或鄉鎮會明確標出，不會用直線硬算
- **AI 決策助手** — 整合規則層 + RAG + 本地 LLM，根據避難所真實資料回答問題，支援語意查詢、地理距離查詢、容量排序查詢、模擬結果查詢；地區 / 鄉鎮 / 設施 / 容量條件轉成 metadata 篩選，範圍外縣市直接拒答
- **英文模式** — 一鍵把介面與回答切成英文；避難所名稱保留中文，防災知識段落的翻譯審過後才切成英文。
- **防災知識** — 「颱風前要準備什麼」「土石流黃色警戒是什麼意思」這類問題，依中央氣象署、內政部消防署、農村水保署官方頁面改寫的 34 段知識文件回答，每個回答最後都附上出處

## 技術架構

```
資料層        PostgreSQL + PostGIS + pgRouting（空間資料、路網）
              ChromaDB（語意向量索引）

後端層        FastAPI + Uvicorn
              ShelterRepository（資料庫操作，連線池）
              ChatService（意圖判斷 + RAG + LLM）
              VectorStore（ChromaDB 管理）
              KnowledgeStore（防災知識文件，獨立的 collection）

前端層        Leaflet.js + OpenStreetMap
              Canvas API（人群疏散動畫）
              iOS 風格 UI

容器化        Docker + Docker Compose
LLM           Ollama + llama3.2:3b（宿主機本地部署）
```

## 專案結構

```
DisasterHub/
├── README.md / README.zh-TW.md # 首頁（英文 / 繁體中文）
├── LICENSE                     # MIT（只涵蓋程式碼，資料沿用各自的授權）
├── docs/                       # 詳細文件；中文版在 zh-TW/，截圖在 images/
├── app.py                      # FastAPI 主程式
├── config.py                   # 所有環境變數集中處
├── Dockerfile                  # INSTALL_DEV build arg 控制 dev 依賴
├── docker-compose.yml          # 正式設定
├── docker-compose.dev.yml      # 開發疊加（熱重載 / 原始碼掛載 / 測試依賴）
├── ruff.toml                   # Lint 設定（CI 跑 `ruff check .`）
├── requirements.txt            # 執行期依賴（意圖：版本範圍）
├── requirements-dev.txt        # 測試依賴（pytest / httpx）
├── requirements.lock           # 完整釘選的執行期版本 — 映像實際安裝的是這個
├── requirements-dev.lock       # 完整釘選的測試版本
├── init.sql                    # 資料庫初始化（首次啟動自動執行）
├── .env.example
├── .github/workflows/ci.yml    # push / PR 自動跑測試
├── conftest.py                 # 測試用環境變數預設值
│
├── models/
│   └── shelter.py              # Shelter 與 NearbyShelter（避難所 + 該次查詢的距離）
│
├── repositories/
│   └── shelter_repository.py   # PostGIS 查詢（連線池、批次 upsert、schema migration）
│
├── services/
│   ├── chat/                   # 問答流程
│   │   ├── service.py          # 聊天流程：選路由、組資料、串流回答、回答防護
│   │   ├── intent.py           # 問句走哪條路（最近、模擬、追問）、抓座標
│   │   ├── replies.py          # 固定回覆與英文翻譯
│   │   ├── llm_client.py       # 呼叫 Ollama：系統提示、請求內容、有時限的串流生成
│   │   ├── query_rules.py      # 查詢規則層：地區 / 鄉鎮 / 設施 / 容量 → metadata 篩選，範圍外拒答
│   │   ├── direct_answer.py    # 避難所清單、容量排名、概況、點名由程式排版（不經過 LLM）
│   │   ├── answer_guard.py     # 回覆中提到 prompt 資料裡沒有的場所，整行拿掉
│   │   └── shelter_names.py    # 顯示名稱、別名、錯字說明、查無此間的回覆
│   ├── retrieval/              # 檢索
│   │   ├── vector_store.py     # ChromaDB 向量索引（支援局部更新）
│   │   ├── embeddings.py       # Ollama / MiniLM embedding
│   │   ├── knowledge.py        # 防災知識文件：解析、索引、檢索、回答組字、出處
│   │   └── shelter_profile.py  # 名稱/地址 → 鄉鎮、設施、別名
│   ├── simulation/             # 災害模擬
│   │   ├── evacuation.py       # 疏散分配：鄉鎮 → 避難所，近者優先、不超過容量
│   │   ├── population.py       # 海岸線 + 鄉鎮人口模型，疏散人數估算
│   │   └── text.py             # 模擬結果寫成文字：人口、分配、潛勢範圍與路線說明
│   ├── geo/                    # 路網與警戒
│   │   ├── routing.py          # 最近避難所與疏散分配的道路距離，算不出來退回直線距離
│   │   └── hazard_alerts.py    # 即時土石流警戒（農村水保署開放資料），背景定期抓取
│   ├── ingest/                 # 資料匯入
│   │   ├── fetcher.py          # 讀取 JSON 資料
│   │   └── sync.py             # 資料同步
│   └── web/                    # API 周邊
│       ├── auth.py             # 登入：session cookie 與寫入端點的權限
│       ├── rate_limit.py       # 按來源的滑動視窗用量上限（聊天）
│       ├── health.py           # readiness 依賴檢查
│       ├── map_service.py      # 領域物件 → API JSON（dict 只在這一層產生）
│       ├── metrics.py          # 記憶體計數器與延遲取樣（/api/stats）
│       └── i18n.py             # 英文模式：鄉鎮、設施、災害的英文名稱與語言代碼
│
├── data_for_refuge/
│   ├── hualien_shelter.json    # 花蓮（197 筆，消防署）
│   ├── taitung_shelter.json    # 台東（198 筆）
│   └── yilan_shelter.json      # 宜蘭（249 筆）
│
├── data_reference/
│   ├── east_taiwan_population.json  # 海岸線折線 + 29 個鄉鎮人口（前端動畫與 AI 共用）
│   ├── debris_flow_east.geojson     # 宜花東土石流潛勢溪流影響範圍（農業部）
│   ├── east_roads.json.gz           # 宜花東路網（OpenStreetMap，ODbL）
│   ├── shelter_coordinate_fixes.json # 用 OpenStreetMap 修正的官方避難所座標
│   └── knowledge/                   # 防災知識文件，每種災害一個 Markdown 檔（格式見資料夾內 README）
│
├── tools/
│   ├── build_hazard_zones.py   # 農業部 SHP → debris_flow_east.geojson（TWD97 → WGS84）
│   ├── build_shelters.py       # 消防署 CSV → data_for_refuge/*.json
│   └── build_road_network.py   # OpenStreetMap PBF → east_roads.json.gz（pgRouting 邊表）
│
├── static/
│   ├── index.html              # 頁面骨架
│   ├── style.css               # 介面樣式
│   ├── i18n.js                 # 介面的中英文字串與語言切換
│   └── app.js                  # 地圖 / 模擬 / 聊天邏輯
│
└── tests/
    ├── conftest.py             # 共用 fixture（連真實資料庫的 repository）
    ├── chat/
    │   ├── test_chat_service.py
    │   ├── test_query_rules.py
    │   ├── test_direct_answer.py
    │   └── test_i18n.py
    ├── retrieval/
    │   ├── test_vector_store.py
    │   ├── test_knowledge.py
    │   └── test_shelter_profile.py
    ├── simulation/
    │   ├── test_evacuation.py
    │   └── test_population_service.py
    ├── geo/
    │   ├── test_routing.py
    │   ├── test_hazard_alerts.py
    │   └── test_hazard_zones.py
    ├── ingest/
    │   ├── test_sync_service.py
    │   ├── test_data_fetcher2.py
    │   ├── test_shelter_repository.py
    │   └── test_shelter_model.py
    ├── web/
    │   ├── test_api_data.py
    │   ├── test_auth.py
    │   ├── test_rate_limit.py
    │   ├── test_metrics.py
    │   ├── test_map_service.py
    │   └── test_config.py
    └── evals/
        ├── test_route_eval.py
        ├── test_boundary_eval.py
        ├── test_knowledge_eval.py
        ├── test_gen_metrics.py
        ├── test_gen_eval_progress.py
        ├── test_faithfulness.py
        ├── test_eval_metrics.py
        └── test_bootstrap.py
```

## 避難所資料

避難所來自消防署開放資料「避難收容處所點位檔」（[data.gov.tw/dataset/73242](https://data.gov.tw/dataset/73242)，2025-12-16 更新，政府資料開放授權條款第 1 版）。2026 年 10 月以前用的是手動整理的 50 間；其中只有 9 間在官方名單上，而且容量對不上（澳花國小原本寫 150 人，官方是 50 人），所以整份換掉，不做合併。

- **重建資料**：把 CSV 下載到專案外的獨立資料夾，執行 `python tools/build_shelters.py <csv>`，會寫出 `data_for_refuge/{yilan,hualien,taitung}_shelter.json`。每行一間，重建後的 diff 直接看得出哪幾間變了。
- **欄位**：除了名稱、容量（預計收容人數）、地址與座標，每間還有鄉鎮與村里、適用災害（`hazards`：水災／震災／土石流／海嘯）、室內／室外、是否適合避難弱者、預計收容村里（`serves`）與聯絡電話。管理人姓名不收。
- **清理**：三縣原始資料 650 筆，保留 644 筆。
  - 座標不在所屬縣內的丟掉：澳花國小被放在新竹山區、礁溪鄉公所在福建、長濱三間活動中心在臺北市。
  - 離同鄉鎮其他避難所中位位置超過 40 公里的也丟掉：賓朗村活動中心被放在花蓮，緯度差了 0.7 度。
  - 其中 4 筆（澳花國小、礁溪鄉公所二樓、介達國小、大南國民小學）在 OpenStreetMap 上名稱與地址都對得上，改用 OSM 的位置保留，連同 OSM 編號記在 `data_reference/shelter_coordinate_fixes.json`。
  - 另外 6 筆直接丟掉。門牌差一號（474巷3號對 474巷3-1號）不算對上。
  - 同一縣裡同名的加上鄉鎮，例如「南興社區活動中心（冬山鄉）」，因為名稱是資料庫的唯一鍵。
  - 名稱後面夾的英文（「介達國小 Jieda Elementary School」）去掉，地址補上縣市鄉鎮。
- **來源資料已知的問題**：
  - 有 12 組座標被多間共用，長濱有 4 間在同一點，大概是村辦公處的位置。
  - 8 間收容人數填 0，保留地點，但不會被分配到人。
  - 少數罕用字在上游就遺失了，顯示成 `?`（台?基督教會）。
  - 有一個名稱被截斷（行政院國軍退除役官兵輔導委員會花）。
- **資料表**：新欄位用 `ADD COLUMN IF NOT EXISTS` 補進既有資料庫，下一次同步就會填好。同步時「清掉來源已移除的」那一步會刪掉不在官方名單上的 41 間舊資料。

**新欄位的用途**

- **模擬**：適用災害不含這次災害類型的避難所，不列為疏散目的地。淹水要列「水災」、地震要列「震災」；火災沒有對應的類別。
  - 類別欄空白的 51 間是來源沒填，照常分配。
  - 回應的 `undesignated` 列出被排除的避難所，`designation` 是對應的類別；AI 的受影響清單會逐間標註，附加在回答後的說明也會列出全部名稱。
  - 花蓮市半徑 10 公里淹水，65 間裡排除 7 間；宜蘭市半徑 10 公里地震，140 間裡排除 6 間。
- **查詢規則**：
  - 地震、淹水、颱風、土石流、海嘯對應適用災害；「適合長者」「行動不便的人可以去哪」「無障礙」對應適合避難弱者；「室內」對應室內場地。
  - 這三種條件只收窄已經有的地點、設施或容量篩選，自己不構成篩選：只問「地震了要去哪裡」會篩出 442 間，又沒有地點可以排序。
  - 單獨出現「老人」不算條件，因為「大武老人會館」是名稱。
  - 鄉鎮改用三縣全部 41 個的固定清單，不再取自人口模型的 29 個；「臺」和「台」視為同一個字。
  - 離題詞如果是避難所名稱的一部分（國軍805醫院、元新溫泉飯店），不再被當成離題拒答。
- **設施類型**：新增活動中心、宗教場所、大學、幼兒園、社福機構、軍事營區、旅館。舊規則下 644 間有 505 間落在「其他」，現在剩 48 間。
- **規模分級**：小型 ≤ 50 人、中型 ≤ 150 人、其餘為大型，大約各占 40% / 41% / 19%。舊的 300 / 800 分界會把幾乎所有官方避難所都歸成小型。收容人數為 0 的分級是「未提供」，文件也寫成「收容人數資料未提供」，因為「容量 0 人、0 個空位」會被讀成已經滿了。
- **文件與地圖**：每間避難所的向量文件最後加上適用災害、室內／室外、是否適合避難弱者、預計收容村里與電話。來源沒填的欄位直接省略，不寫「未知」，因為模型會把「未知」講成「不適合」。地圖 popup 顯示同樣的欄位，標記大小改成依容量的平方根縮放。

**644 間時怎麼回答**

- **改給概況，不再列長清單**：「宜蘭有哪些避難所」現在符合 249 間。
  - 以前送最相關的 30 筆全文，大約 4,800 個 prompt token，放得進 8,192 的上下文；但 30 間一間一行需要超過 800 token 的輸出上限，回答會被截斷，而且 30 間本來就不是「全部」。
  - 現在符合超過 15 間時，prompt 改給概況：總數、容量合計與剩餘空間（另註明幾間沒提供收容人數）、依鄉鎮的分布（已指定鄉鎮時改依設施類型），以及最相關的 10 間。
  - 模型被要求先說明總數與分布，列出這 10 間，再建議使用者縮小範圍。`retrieval.overview` 計數這類回答。
- **完整名稱直接取**：問句裡有某間避難所的完整名稱時，在向量檢索之前先用 metadata 直接取那一間。內城國民中小學化育分校這種長的官方名稱，bge-m3 的前 10 名裡實測沒有它。
- **離題判斷一律參考避難所名稱**：「離題詞是避難所名稱的一部分」的判斷移進 `query_rules.analyze(known_names=…)`，檢索評測和聊天走同一條路。以前聊天會回答「805醫院」，評測裡卻一筆都撈不到。
- **長官方名稱的別名**：
  - 開頭地名改用 41 個鄉鎮全部，去掉地名後也產生簡稱：花蓮縣鶴岡國民小學 → 鶴岡國小，所以「鶴崗國小」會被改成正確名稱，不再回報查無。
  - 地名本身就是名稱的一部分時（成功國小體育館 → 國小體育館、大同活動中心 → 活動中心）不產生別名。這種通用別名會讓錯字比對同時像好幾間，結果一間都改不了（「公證國小體育館」一度改不回來）。

檢索評測集已針對新資料重新產生：207 題。名稱題改成抽樣，題庫維持原本 50 間時的規模；人工標註的別名與語意題也改用新名稱重寫。邊界評測裡的點名、錯字、近名題也改寫了，每一題都先用實際的錯字與查無規則驗證過。例如礁溪國小、蘇澳國中以前是「不存在的近名」，官方資料裡卻有，所以改成礁溪高中、蘇澳高中。

## dict 在哪裡產生

`ShelterRepository` 一律回領域物件——`list[Shelter]`，或 `list[NearbyShelter]`（避難所加上
這一次查詢算出的距離；距離不是避難所的屬性）。要送出去當 JSON 的 `dict` 只在 `MapService`
產生，`current_people` → `current_ppl` 的改名也只發生在那一層。

這個改名，以及 `/api/shelters`、`/api/simulate_disaster`、`/api/nearest_shelter` 的欄位組合，
都是對外契約：前端的疏散動畫讀 `remaining` / `lat` / `lon`，收容人數回寫讀 `name`。
`tests/web/test_map_service.py` 把欄位組合釘死，欄位改名就不會讓地圖安靜地壞掉。
