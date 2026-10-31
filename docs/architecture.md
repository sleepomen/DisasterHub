# Architecture

[← README](../README.md) · English · [繁體中文](zh-TW/architecture.md)

<p align="center"><img src="images/map.jpg" alt="Map of the 644 official shelters" width="100%"></p>

## Features

- **Real map visualization** — Leaflet.js + OpenStreetMap. 644 official shelters (National Fire Agency open data) across Yilan, Hualien and Taitung are plotted at their real coordinates; marker size scales with capacity, and clicking one shows live information.
- **PostGIS spatial simulation** — Set a disaster epicenter and impact radius; the backend computes the list of affected shelters via `ST_DWithin`. Earthquake, flood and fire scenarios are supported.
- **Road routing** — Nearest-shelter answers and evacuation plans follow the OpenStreetMap road network (pgRouting), not straight lines. The shelter reached fastest on foot or by car comes first, the actual routes are drawn on the map, and the evacuees in the animation move along them.
- **Crowd evacuation animation** — The backend estimates the population inside the impact radius and the resulting evacuation demand from a township population model, then computes a deterministic, capacity-bounded evacuation plan (nearest first) and writes it to the database and the vector index straight away. The frontend only replays that plan as an animation, so the loads the AI assistant reports match the map as soon as the simulation returns.
- **Debris-flow hazard zones** — 531 debris-flow impact zones in Yilan, Hualien and Taitung from the Ministry of Agriculture are imported into PostGIS and drawn on the map. Flood simulations keep evacuees out of shelters inside a zone, and nearest-shelter answers warn when a shelter lies inside one.
- **Hazard-aware routes and live alerts** — Flood routes bend around debris-flow zones. Debris-flow alerts from the Agency of Rural Development and Soil and Water Conservation are polled every 10 minutes: roads inside a red-alert zone are closed, roads inside a yellow-alert zone are avoided where possible, and a shelter or township with no safe road is reported as such instead of being routed in a straight line.
- **AI decision assistant** — A rules layer plus RAG and a local LLM answer questions from real shelter data, covering semantic lookups, geographic distance queries, capacity rankings, and simulation-result queries. Region, township, facility and capacity conditions become metadata filters, and questions about counties outside the coverage area are declined outright.
- **English mode** — A toggle switches the interface and the answers to English; shelter names stay in Chinese, and preparedness passages switch to English once their translation is reviewed.
- **Disaster-preparedness knowledge** — "What should I prepare before a typhoon?" or "What does a yellow debris-flow alert mean?" is answered from 34 short passages rewritten from official Central Weather Administration, National Fire Agency and Agency of Rural Development and Soil and Water Conservation pages, with the sources appended to every answer.

## Architecture

```
Data          PostgreSQL + PostGIS + pgRouting (spatial data, road network)
              ChromaDB (semantic vector index)

Backend       FastAPI + Uvicorn
              ShelterRepository (database access, connection pool)
              ChatService (intent detection + RAG + LLM)
              VectorStore (ChromaDB management)
              KnowledgeStore (disaster-preparedness passages, separate collection)

Frontend      Leaflet.js + OpenStreetMap
              Canvas API (crowd evacuation animation)
              iOS-style UI

Containers    Docker + Docker Compose
LLM           Ollama + llama3.2:3b (running locally on the host)
```

## Project Structure

```
DisasterHub/
├── README.md / README.zh-TW.md # Front page (English / 繁體中文)
├── docs/                       # Detailed documentation; zh-TW/ holds the Chinese versions, images/ the screenshots
├── app.py                      # FastAPI application
├── config.py                   # Single place for all environment variables
├── Dockerfile                  # INSTALL_DEV build arg controls dev dependencies
├── docker-compose.yml          # Production setup
├── docker-compose.dev.yml      # Dev overlay (hot reload / source mount / test deps)
├── ruff.toml                   # Lint config (CI runs `ruff check .`)
├── requirements.txt            # Runtime dependencies (intent: version ranges)
├── requirements-dev.txt        # Test dependencies (pytest / httpx)
├── requirements.lock           # Fully pinned runtime set — what the image installs
├── requirements-dev.lock       # Fully pinned test set
├── init.sql                    # Database bootstrap (runs automatically on first start)
├── .env.example
├── .github/workflows/ci.yml    # Runs the tests on push / PR
├── conftest.py                 # Default environment variables for tests
│
├── models/
│   └── shelter.py              # Shelter + NearbyShelter (shelter plus a per-query distance)
│
├── repositories/
│   └── shelter_repository.py   # PostGIS queries (pooling, batch upsert, schema migration)
│
├── services/
│   ├── chat/                   # Question answering
│   │   ├── service.py          # Chat flow: picks the route, builds the context, streams the reply, guards it
│   │   ├── intent.py           # Which route a question takes (nearest / simulation / follow-up), coordinate parsing
│   │   ├── replies.py          # Fixed replies and their English versions
│   │   ├── llm_client.py       # Ollama generation: system prompts, request, streaming with a deadline
│   │   ├── query_rules.py      # Query rules layer: region / township / facility / capacity → metadata filters, out-of-scope rejection
│   │   ├── direct_answer.py    # Shelter lists, rankings, overviews and named shelters formatted by code (no LLM)
│   │   ├── answer_guard.py     # Drops reply lines naming a place that is not in the prompt's shelter data
│   │   └── shelter_names.py    # Display names, aliases, typo notes, "no such shelter" replies
│   ├── retrieval/              # Retrieval
│   │   ├── vector_store.py     # ChromaDB vector index (supports partial updates)
│   │   ├── embeddings.py       # Ollama / MiniLM embeddings
│   │   ├── knowledge.py        # Disaster-preparedness passages: parsing, index, retrieval, answer text, source lines
│   │   └── shelter_profile.py  # Name/address → township, facilities, aliases
│   ├── simulation/             # Disaster simulation
│   │   ├── evacuation.py       # Evacuation plan: townships → shelters, nearest first, capacity-bounded
│   │   ├── population.py       # Coastline + township population model, evacuee estimation
│   │   └── text.py             # Simulation results as text: population, allocation, hazard and route notes
│   ├── geo/                    # Road network and alerts
│   │   ├── routing.py          # Road distances for nearest shelters and evacuation, straight-line fallback
│   │   └── hazard_alerts.py    # Live debris-flow alerts (SWCB open data), polled in the background
│   ├── ingest/                 # Data import
│   │   ├── fetcher.py          # Reads the JSON data
│   │   └── sync.py             # Data synchronization
│   └── web/                    # API plumbing
│       ├── auth.py             # Login: session cookie and access to write endpoints
│       ├── rate_limit.py       # Per-source sliding-window request limit (chat)
│       ├── health.py           # Readiness dependency checks
│       ├── map_service.py      # Domain objects → API JSON (the only place dicts are built)
│       ├── metrics.py          # In-memory counters + latency samples (/api/stats)
│       └── i18n.py             # English mode: township / facility / hazard names, language codes
│
├── data_for_refuge/
│   ├── hualien_shelter.json    # Hualien (197 entries, National Fire Agency)
│   ├── taitung_shelter.json    # Taitung (198 entries)
│   └── yilan_shelter.json      # Yilan (249 entries)
│
├── data_reference/
│   ├── east_taiwan_population.json  # Coastline polylines + population of 29 townships (shared by the frontend animation and the AI)
│   ├── debris_flow_east.geojson     # Debris-flow impact zones in the three eastern counties (Ministry of Agriculture)
│   ├── east_roads.json.gz           # Road network of the three eastern counties (OpenStreetMap, ODbL)
│   ├── shelter_coordinate_fixes.json # Official shelter coordinates corrected from OpenStreetMap
│   └── knowledge/                   # Disaster-preparedness passages, one Markdown file per hazard (README explains the format)
│
├── tools/
│   ├── build_hazard_zones.py   # Ministry of Agriculture SHP → debris_flow_east.geojson (TWD97 → WGS84)
│   ├── build_shelters.py       # National Fire Agency CSV → data_for_refuge/*.json
│   └── build_road_network.py   # OpenStreetMap PBF → east_roads.json.gz (pgRouting edge table)
│
├── static/
│   ├── index.html              # Page skeleton
│   ├── style.css               # UI styles
│   ├── i18n.js                 # Interface strings in Chinese and English, language toggle
│   └── app.js                  # Map / simulation / chat logic
│
└── tests/
    ├── conftest.py             # Shared fixtures (real-database repository)
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

## Shelter Data

The shelters come from the National Fire Agency's open dataset 避難收容處所點位檔 ([data.gov.tw/dataset/73242](https://data.gov.tw/dataset/73242), updated 2025-12-16, Open Government Data License v1). Until October 2026 the project used 50 hand-compiled shelters; only 9 of them appear in the official list, and their capacities did not match (澳花國小 was 150 there, 50 officially), so the data was replaced rather than merged.

- **Rebuilding.** Download the CSV into its own folder outside the project and run `python tools/build_shelters.py <csv>`. It writes `data_for_refuge/{yilan,hualien,taitung}_shelter.json`, one shelter per line so a rebuild shows up in the diff shelter by shelter.
- **Fields.** Besides name, capacity (預計收容人數), address and coordinates, each shelter carries its township and village, the disasters it is designated for (`hazards`: 水災 / 震災 / 土石流 / 海嘯), indoor / outdoor, whether it suits vulnerable evacuees, the villages it is planned to receive (`serves`) and the contact phone. The manager's personal name is not imported.
- **Cleaning.** The three counties have 650 rows; 644 are kept.
  - Coordinates outside the county are dropped: 澳花國小 sat in the Hsinchu mountains, 礁溪鄉公所 in Fujian, and three Changbin activity centres in Taipei.
  - Coordinates more than 40 km from the median of the same township's other shelters are also dropped. 賓朗村活動中心 was placed in Hualien, 0.7° of latitude off.
  - Four of these (澳花國小, 礁溪鄉公所二樓, 介達國小, 大南國民小學) match an OpenStreetMap object on both name and address. They are kept at the OSM position, recorded with the OSM id in `data_reference/shelter_coordinate_fixes.json`.
  - The other six are dropped. A one-digit house-number mismatch (474巷3號 vs 474巷3-1號) is not treated as a match.
  - Names that repeat within a county get their township appended, e.g. 南興社區活動中心（冬山鄉）, since the name is the database key.
  - English tails such as 介達國小 Jieda Elementary School are removed, and addresses are completed with county and township.
- **Known quirks of the source.**
  - 12 coordinate pairs are shared by several shelters; four in Changbin share one point, probably the village office.
  - 8 shelters list capacity 0. They are kept as locations but never receive evacuees.
  - A few rare characters were lost upstream and appear as `?` (台?基督教會).
  - One name is truncated (行政院國軍退除役官兵輔導委員會花).
- **Schema.** The new columns are added to existing databases with `ADD COLUMN IF NOT EXISTS` and filled by the next sync. The sync's delete-missing step removes the 41 old shelters that are not in the official list.

**How the new fields are used.**

- **Simulation.** Shelters whose official disaster types do not include the simulated one are not destinations. Floods require 水災 and earthquakes 震災; fire has no matching type.
  - A blank type list (51 shelters) means the source left it empty, so those shelters stay eligible.
  - The response lists the excluded shelters in `undesignated` (with `designation` naming the type). The AI's affected-shelter list marks each one, and the appended note names them all.
  - A 10 km flood around Hualien City excludes 7 of 65; a 10 km earthquake around Yilan City excludes 6 of 140.
- **Query rules.**
  - 地震 / 淹水 / 颱風 / 土石流 / 海嘯 map to the disaster type. 「適合長者」「行動不便的人可以去哪」「無障礙」 mean suitable for vulnerable evacuees; 室內 means indoor.
  - These conditions only narrow an existing place, facility or capacity filter. 「地震了要去哪裡」 on its own would otherwise match 442 shelters with no place to rank them by.
  - The bare word 老人 is not a condition, because 大武老人會館 is a name.
  - Townships come from a fixed list of all 41 in the three counties, not the 29 in the population model, and 臺 / 台 are treated as the same.
  - An off-topic word that is part of a shelter name (國軍805醫院, 元新溫泉飯店) no longer triggers the off-topic refusal.
- **Facility types.** New labels: 活動中心, 宗教場所, 大學, 幼兒園, 社福機構, 軍事營區, 旅館. Under the old rules 505 of the 644 shelters fell into 其他; now 48 do.
- **Size classes.** Small is ≤ 50 people, medium ≤ 150, large above that, roughly 40 / 41 / 19 %. The old 300 / 800 split put almost every official shelter in "small". A capacity of 0 is 未提供, and the document says the capacity was not provided, because 「容量 0 人、0 個空位」 reads as "full".
- **Documents and map.** Each shelter's vector document now ends with its disaster types, indoor / outdoor, suitability for vulnerable evacuees, planned villages and phone. Fields the source left empty are omitted rather than written as "unknown", which the model read as "no". The map popup shows the same fields, and marker size scales with the square root of capacity.

**Answering at 644 shelters.**

- **Overview instead of a long list.** 「宜蘭有哪些避難所」 now matches 249 shelters.
  - The old path sent the 30 most relevant full documents. That is about 4,800 prompt tokens, which fits the 8,192-token context, but listing 30 shelters one per line needs more than the 800-token output budget, so answers were cut off. Thirty was never "all" either.
  - When more than 15 shelters match, the prompt now gets an overview: total count, total capacity and remaining space (plus how many have no capacity listed), a breakdown by township (or by facility type when a township is already given) and the 10 most relevant shelters.
  - The model is told to state the total and the distribution, list those 10 and suggest narrowing the question. `retrieval.overview` counts these answers.
- **Full names are fetched directly.** If the question contains a complete shelter name, that shelter is fetched by metadata before dense retrieval runs. Long official names such as 內城國民中小學化育分校 were not in bge-m3's top 10.
- **Off-topic check uses shelter names everywhere.** The "this off-topic word is part of a shelter name" check moved into `query_rules.analyze(known_names=…)`, so the retrieval eval follows the same path as the chat. Before, 「805醫院」 was answered in chat but retrieved nothing in the eval.
- **Aliases for long official names.**
  - Leading place names now cover all 41 townships, and stripping one also yields the short form: 花蓮縣鶴岡國民小學 → 鶴岡國小, so 「鶴崗國小」 is corrected instead of reported as nonexistent.
  - When the place name is the name itself (成功國小體育館 → 國小體育館, 大同活動中心 → 活動中心), no alias is produced. Such generic aliases made the typo matcher see several candidates and correct none (「公證國小體育館」 stopped resolving).

The retrieval eval set was regenerated for this data (207 questions; name questions are sampled so the set stays the size it had with 50 shelters, and the hand-labeled alias / semantic cases were rewritten against the new names). The boundary eval's named, misspelled and near-name questions were rewritten too, and each replacement was checked against the actual typo and unknown-name rules. For example, 礁溪國小 and 蘇澳國中 used to be nonexistent near-names but exist in the official data, so they became 礁溪高中 and 蘇澳高中.

## Where dicts are built

`ShelterRepository` returns domain objects everywhere — `list[Shelter]`, or `list[NearbyShelter]`
(a shelter plus the distance that one query computed, which is not a property of the shelter). The
`dict`s that go out as JSON are built only in `MapService`, which is also the only place the
`current_people` → `current_ppl` rename happens.

That rename, and the key sets for `/api/shelters`, `/api/simulate_disaster` and `/api/nearest_shelter`,
are an external contract: the frontend's evacuation animation reads `remaining` / `lat` / `lon`, and the
occupancy write-back reads `name`. `tests/web/test_map_service.py` pins the exact key sets so a field rename
cannot break the map silently.
