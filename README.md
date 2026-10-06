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
| `CHAT_RATE_LIMIT` / `CHAT_RATE_WINDOW` | How many `/api/chat` questions one source may ask per window, default 15 per 60 s. The concurrency cap only limits how many run at once; this limits how many one client can ask at all, because chat needs no login. Set `CHAT_RATE_LIMIT=0` to disable |
| `DB_CONNECT_TIMEOUT` / `DB_STATEMENT_TIMEOUT_MS` | Database connect timeout (seconds, default 5) and per-statement timeout (milliseconds, default 15000), so a hung database fails requests within seconds instead of blocking every worker thread |

Optional variables: `OLLAMA_MODEL` (default `llama3.2:3b`), `OLLAMA_TEMPERATURE`, `OLLAMA_NUM_CTX` (default 8192 — must hold the 20–30 shelter documents a list-style query retrieves), `OLLAMA_NUM_PREDICT` (default 800), `OLLAMA_TIMEOUT`, `EMBEDDING_PROVIDER` (`ollama` or `minilm`), `EMBEDDING_MODEL` (default `bge-m3`), `EMBEDDING_TIMEOUT`, `RAG_TOP_K`, `RAG_MAX_DISTANCE` (default 0.56 — cosine distance above which a plain semantic search is treated as "no relevant data"; questions about the weather or subsidies then get a fixed reply instead of ten unrelated shelters), `DB_POOL_MIN` / `DB_POOL_MAX` (default 1 / 10).

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
│   ├── data_fetcher.py         # Reads the JSON data
│   ├── map_service.py          # Domain objects → API JSON (the only place dicts are built)
│   ├── metrics.py              # In-memory counters + latency samples (/api/stats)
│   ├── rate_limit.py           # Per-source sliding-window request limit (chat)
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
    ├── test_metrics.py
    ├── test_rate_limit.py
    ├── test_data_fetcher2.py
    ├── test_shelter_profile.py
    ├── test_shelter_repository.py
    ├── test_population_service.py
    ├── test_query_rules.py
    ├── test_query_rules.py
    ├── test_vector_store.py
    ├── test_chat_service.py
    ├── test_eval_metrics.py
    ├── test_gen_metrics.py
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
| GET | `/api/stats` | Online counters and latency percentiles (requires login, or `X-API-Key`) |
| POST | `/api/login` | Log in with the administrator credentials; sets an HttpOnly session cookie. Five consecutive failures lock the source for 5 minutes |
| POST | `/api/logout` | Log out and clear the cookie |
| GET | `/api/me` | Current login state; the frontend uses it on load to decide whether to show the login panel |
| POST | `/api/simulate_disaster` | Run the spatial disaster simulation; returns the affected shelters plus estimates of the population inside the radius, the evacuation demand, and the shelter shortfall (requires login, or `X-API-Key`) |
| POST | `/api/occupancy` | Write back each shelter's current occupancy (called by the frontend once the evacuation animation ends); only the changed vector documents are recomputed (requires login, or `X-API-Key`) |
| POST | `/api/reset_simulation` | Clear the simulation state and restore occupancy to the initial values from the source data (requires login, or `X-API-Key`) |
| POST | `/api/nearest_shelter` | Find the nearest shelters (PostGIS distance ordering) |
| POST | `/api/chat` | AI decision assistant. Streams the answer back as Server-Sent Events. Returns 429 when all `CHAT_MAX_CONCURRENT` generation slots are busy |
| POST | `/api/sync` | Trigger a data sync manually. Accepts `SYNC_API_KEY`, a logged-in session, or `WRITE_API_KEY` — the startup sync can fail, and an operator must be able to recover without restarting the container. The index is rebuilt into a staging collection and swapped in atomically, so chat keeps answering from the old index while embeddings are recomputed |

`/health/ready` treats the database and the vector index as hard requirements — restarting the container can recover them. Ollama is only reported, never decisive, because it runs on the host and restarting the container cannot fix it. The container `HEALTHCHECK` hits this endpoint. Readiness also stays answerable while Ollama is slow: query and document embeddings are computed outside the index lock, so a stalled embedding call cannot block the `count()` the index check performs. If the sync at startup fails the service still starts and retries it in the background with backoff (5 s, 15 s, 30 s, 60 s, 120 s, 300 s) until it succeeds — a database that was not up yet, or an Ollama still loading the embedding model, fixes itself inside that window; until then readiness stays 503.

Manual sync example:

```bash
curl -X POST http://localhost:8501/api/sync -H "X-API-Key: $SYNC_API_KEY"
```

### Streaming the Chat Answer

`/api/chat` replies with `text/event-stream` rather than one JSON body. A county-wide listing takes the model about a minute (`region` p50 is roughly 60 s on llama3.2:3b and 80 s on qwen2.5:7b), and a spinner that long is indistinguishable from a hang. Each event is one JSON object on a `data:` line:

```
data: {"type": "delta", "text": "甲避難所：容量 100 人"}
data: {"type": "delta", "text": "、乙避難所：容量 300 人"}
data: {"type": "done"}
```

- `delta` — a fragment of the answer; append it as it arrives. Replies the retrieval layer can produce on its own (out of scope, "please give me your coordinates", no relevant data) arrive as a single `delta` without the LLM being called at all.
- `error` — a message meant for the user. If `delta` events were already sent, the stream was cut mid-answer: the text already on screen stays and the notice is appended. If it is the first event, generation never produced anything.
- `done` — end of stream.

A generation slot is held for the whole stream, including when the client closes the tab, so `CHAT_MAX_CONCURRENT` still bounds concurrent generations. One generation is still capped at `OLLAMA_TIMEOUT` seconds, now enforced by a deadline inside the stream loop — in streaming mode a read timeout only limits the gap between two chunks, not the total.

```bash
curl -N -X POST http://localhost:8501/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "宜蘭有哪些避難所"}'
```

Behind a reverse proxy, response buffering has to be off or the stream is pointless: the endpoint sends `X-Accel-Buffering: no` for nginx, and Caddy does not buffer by default.

### Where dicts are built

`ShelterRepository` returns domain objects everywhere — `list[Shelter]`, or `list[NearbyShelter]`
(a shelter plus the distance that one query computed, which is not a property of the shelter). The
`dict`s that go out as JSON are built only in `MapService`, which is also the only place the
`current_people` → `current_ppl` rename happens.

That rename, and the key sets for `/api/shelters`, `/api/simulate_disaster` and `/api/nearest_shelter`,
are an external contract: the frontend's evacuation animation reads `remaining` / `lat` / `lon`, and the
occupancy write-back reads `name`. `tests/test_map_service.py` pins the exact key sets so a field rename
cannot break the map silently.

### Online Counters and Logs

The evaluations under `evals/` measure the model against a fixed 130-question set. These counters
measure what real traffic actually does, which is not the same thing — and without them there is no
way to tell whether the questions users ask resemble the ones the system was tuned on.

`GET /api/stats` (same access as the write endpoints — it exposes traffic patterns and throttle state,
so it is not public; use `/health/ready` for external probes) returns:

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

Counter names are registered up front, so one that has never fired still reports `0` — "never happened"
and "no such metric" would otherwise look identical, and these numbers are only meaningful as ratios.
The ones worth watching:

- **`retrieval.fell_back` over `chat.route.rag`** — how often the rules layer extracted a condition that
  nothing in the index matched, so the model was handed "the closest other data" instead. A rising ratio
  means the keyword tables in `services/query_rules.py` are mis-reading real questions.
- **`retrieval.no_match`** — how often the distance gate refused to answer. `RAG_MAX_DISTANCE` was tuned
  on the eval set; this is the only signal for whether it is also right for real questions.
- **`retrieval.truncated`** — results that hit `MAX_FILTERED_RESULTS`, so the prompt saw fewer shelters
  than exist. Zero at the current data size; it starts firing when a county passes 30 shelters.
- **`chat.rejected.rate_limit` vs `chat.rejected.busy`** — someone hammering the endpoint versus not
  enough generation slots. Both return 429 and need completely different responses.
- **`chat.generation.interrupted`** — answers cut off mid-stream (upstream died, or the `OLLAMA_TIMEOUT`
  deadline fired). Failed generations are sampled into the latency percentiles too, so a timeout cannot
  quietly improve p95.

Everything is in memory and resets on restart; the percentiles cover the most recent 512 samples per
path, not the whole uptime.

Every response carries an `X-Request-ID`, every log line is prefixed with it, and each answer leaves one
`key=value` summary line — so a user reporting "the answer I just got was wrong" gives you the id to grep:

```
2026-10-05 08:59:12 INFO services.chat_service [e943c1f1]: chat route=rag source=generated outcome=ok context_chars=138 reply_chars=34 ms=12164
```

An incoming `X-Request-ID` is only honoured when `TRUST_PROXY_HEADERS=true`, and only if it matches
`[A-Za-z0-9._-]{1,64}` — the value reaches the log, so an unchecked one would let anyone inject newlines
into it.

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
pip install --only-binary :all: -r requirements-dev.lock
pytest tests/ -v
```

Installing from the lock file gets the same versions the image has. Note that `chroma-hnswlib`
(pulled in by `chromadb` 0.5.x) has no wheels beyond CPython 3.12, so a local run needs Python 3.12 or older.

Lint, with the version CI pins:

```bash
pip install ruff==0.16.10
ruff check .
```

CI runs three jobs: `ruff check .`, the tests on a clean Python 3.12 installed from the lock file, and
`docker build` followed by the tests *inside* the image — the last one is what proves the image carries
everything it needs and that the "no compiler required" assumption still holds.

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
│   ├── run_rag_eval.py         # Runs the retrieval evaluation
│   ├── metrics.py              # Recall / Precision / MRR
│   ├── run_gen_eval.py         # Runs the generation evaluation (real ChatService + Ollama)
│   ├── gen_metrics.py          # Answer recall / hallucination / number faithfulness / format rules
│   └── results/                # Output of each run
```

---

## Generation Quality Evaluation

Retrieval recall only says whether the right documents reached the model. `evals/run_gen_eval.py` measures what the model finally says: every question goes through the real `ChatService` path (rules layer, retrieval, prompt, Ollama) and the reply is scored by the rules in `evals/gen_metrics.py`. No LLM judge is involved, so the numbers are reproducible and do not depend on a second model.

```bash
docker exec -it disaster_app python evals/run_gen_eval.py --embedder ollama:bge-m3 --model llama3.2:3b --label gen_llama3.2-3b
docker exec -it disaster_app python evals/run_gen_eval.py --embedder ollama:bge-m3 --model qwen2.5:7b --label gen_qwen2.5-7b
docker exec -it disaster_app python evals/run_gen_eval.py --limit 10 --category region   # quick spot check
```

The same 130-question set is used. `evals/build_eval_set.py` attaches an `expect` block to every positive case with the capacity of each relevant shelter and, for "largest / most" questions, the shelter that must come first.

| Metric | Meaning |
|---|---|
| `ansR` answer recall | Share of the relevant shelters that are actually named in the reply (display name or alias) |
| `ansP` answer precision | Share of the shelters named in the reply that are relevant |
| `full` | Reply names every relevant shelter |
| `halluc` | Reply names a shelter that was not in the retrieved context, or a facility-like name that matches no known shelter |
| `f_abst` false abstain | Reply says "no data" although relevant data was retrieved |
| `num_ok` | Every integer ≥ 50 in the reply also appears in the retrieved context (capacity, occupancy, remaining space are not altered or invented) |
| `cap_ok` | Single-shelter questions: the reply states that shelter's capacity |
| `top1` | Ranking questions: the first shelter named is the correct largest one |
| `fmt_ok` | Traditional Chinese only, no English letters, no simplified characters, no emoji |
| `trunc` | Ollama stopped because `num_predict` ran out (the list was cut off) |
| negatives `abstain_correct` | Out-of-scope or unanswerable questions are declined without naming any shelter |

Each run also records latency p50/p95, output tokens and the full reply per question in `evals/results/<label>.json`.

Results so far (macro averages over the eight positive categories; `negatives` is the separate abstention score, `p50` the median end-to-end latency per question):

| Configuration | ansR | ansP | full | halluc | f_abst | num_ok | fmt_ok | trunc | negatives | p50 |
|---|---|---|---|---|---|---|---|---|---|---|
| llama3.2:3b, prompt before this round (baseline) | 0.725 | 0.719 | 0.707 | 0.022 | 0.214 | 0.986 | 1.000 | 0.014 | 0.900 | 9.4 s |
| **llama3.2:3b, current pipeline** | 0.961 | 0.913 | 0.943 | 0.006 | 0.016 | 0.994 | 1.000 | 0.000 | 1.000 | 8.6 s |
| **qwen2.5:7b, current pipeline** | 0.967 | 0.961 | 0.926 | 0.036 | 0.000 | 1.000 | 1.000 | 0.000 | 1.000 | 21.4 s |

The baseline's dominant failure was not hallucination but over-refusal: on 21% of the questions the model answered "no relevant data" even though the retrieved context listed exactly the shelters it had been asked about. Three changes removed it.

- The system prompt now says the shelter block is pre-filtered by the rules layer, so whatever is listed there must be used; refusing is only allowed when the block is empty or the question is not about shelters at all.
- A question that names a place ("near Zhiben", "around Zhonghua Road section 1") goes to retrieval instead of being bounced back with a request for coordinates.
- When a question names a shelter outright, retrieval keeps only that shelter, and the answer format is fixed at one line per shelter. Together these removed truncation on the 3B model (0.044 → 0.000), which used to answer a single-shelter question by listing ten and running out of output budget.

Out-of-scope questions are now declined by a distance gate rather than by the model: if plain semantic retrieval's closest document is farther than `RAG_MAX_DISTANCE`, a fixed reply is returned and no generation happens. Weather and subsidy questions sit at a cosine distance of 0.588 and above, while the farthest question that does have an answer sits at 0.483.

`capacity_stated` in the baseline row is not comparable with the rows below it: that column now applies only to questions that actually ask about capacity or occupancy, instead of every single-shelter question.

What remains on both models is dropped or mistyped names rather than invented facts: 濤強國小 written as 涙強國小, 中興國小附幼 shortened to 中興國小, 台東縣立體育場 written as 台東縣立體場. That is what most of qwen2.5:7b's `halluc` 0.036 is, and it also explains the recall that is missing from 1.000. Every number either model printed was traceable to the retrieved context, nothing was truncated, and all ten out-of-scope questions were declined. The 3B model's `top1` of 0.667 is one of the three ranking questions, where it named the second-largest shelter first.

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
| `CHAT_RATE_LIMIT` / `CHAT_RATE_WINDOW` | 單一來源在一段時間內最多能問幾題 `/api/chat`，預設 60 秒 15 題。併發上限只擋「同時幾個」，這個擋的是「同一個人總共能問幾次」——聊天端點不需要登入，不然任何人都能拿它當免費 LLM。設 `CHAT_RATE_LIMIT=0` 可停用 |
| `DB_CONNECT_TIMEOUT` / `DB_STATEMENT_TIMEOUT_MS` | 資料庫連線逾時（秒，預設 5）與單一 SQL 逾時（毫秒，預設 15000），資料庫卡住時請求會在幾秒內失敗，不會把所有工作執行緒一起卡死 |

可選變數：`OLLAMA_MODEL`（預設 `llama3.2:3b`）、`OLLAMA_TEMPERATURE`、`OLLAMA_NUM_CTX`（預設 8192，要放得下列舉題一次撈出的 20 至 30 筆避難所文件）、`OLLAMA_NUM_PREDICT`（預設 800）、`OLLAMA_TIMEOUT`、`EMBEDDING_PROVIDER`（`ollama` 或 `minilm`）、`EMBEDDING_MODEL`（預設 `bge-m3`）、`EMBEDDING_TIMEOUT`、`RAG_TOP_K`、`RAG_MAX_DISTANCE`（預設 0.56，純語意檢索的 cosine 距離超過此值就視為沒有相關資料，天氣、補助這類問題會得到固定回覆而不是十筆不相干的避難所）、`DB_POOL_MIN` / `DB_POOL_MAX`（預設 1 / 10）。

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
│   ├── data_fetcher.py         # 讀取 JSON 資料
│   ├── map_service.py          # 領域物件 → API JSON（dict 只在這一層產生）
│   ├── metrics.py              # 記憶體計數器與延遲取樣（/api/stats）
│   ├── rate_limit.py           # 按來源的滑動視窗用量上限（聊天）
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
    ├── test_metrics.py
    ├── test_rate_limit.py
    ├── test_data_fetcher2.py
    ├── test_shelter_profile.py
    ├── test_shelter_repository.py
    ├── test_population_service.py
    ├── test_query_rules.py
    ├── test_vector_store.py
    ├── test_chat_service.py
    ├── test_eval_metrics.py
    ├── test_gen_metrics.py
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
| GET | `/api/stats` | 線上計數器與延遲百分位（需登入，或帶 `X-API-Key`）|
| POST | `/api/login` | 以管理者帳密登入，成功後發 HttpOnly session cookie；連續失敗 5 次會鎖 5 分鐘 |
| POST | `/api/logout` | 登出，清除 cookie |
| GET | `/api/me` | 目前登入狀態，前端載入時用來決定是否顯示登入面板 |
| POST | `/api/simulate_disaster` | 執行災害空間模擬，回傳受影響避難所與圈內人口 / 疏散需求 / 收容缺口估算（需登入，或帶 `X-API-Key`）|
| POST | `/api/occupancy` | 回寫各避難所目前收容人數（疏散動畫結束後由前端呼叫），只重算有變動的向量文件（需登入，或帶 `X-API-Key`）|
| POST | `/api/reset_simulation` | 清除模擬狀態，並把收容人數還原成來源資料的初始值（需登入，或帶 `X-API-Key`）|
| POST | `/api/nearest_shelter` | 查詢最近避難所（PostGIS 距離排序）|
| POST | `/api/chat` | AI 決策助手，回答以 Server-Sent Events 逐段串流。`CHAT_MAX_CONCURRENT` 個生成名額都在忙時回 429 |
| POST | `/api/sync` | 手動觸發資料同步。`SYNC_API_KEY`、登入的 session、`WRITE_API_KEY` 三者任一即可——啟動同步是會失敗的，操作員要能不重啟容器就把資料救回來。索引會先建到暫存 collection 再整個換過去，重算 embedding 期間問答仍用舊索引回答 |

`/health/ready` 把資料庫與向量索引視為必要條件（重啟容器可以恢復），Ollama 只回報狀態不影響判定（它跑在宿主機，重啟容器救不了）。容器的 `HEALTHCHECK` 打的是這支端點。Ollama 變慢時 readiness 也仍然答得出來：查詢與文件的 embedding 都在索引鎖外面算，卡住的 embedding 不會擋住索引檢查用的 `count()`。啟動時的同步失敗不會讓服務起不來，而且會在背景依 5、15、30、60、120、300 秒退避重試直到成功——資料庫還沒起來、Ollama 還在載 embedding 模型都會在這段時間內自己好；在那之前 readiness 會持續回 503。

手動同步範例：

```bash
curl -X POST http://localhost:8501/api/sync -H "X-API-Key: $SYNC_API_KEY"
```

### 聊天回答的串流

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

### dict 在哪裡產生

`ShelterRepository` 一律回領域物件——`list[Shelter]`，或 `list[NearbyShelter]`（避難所加上
這一次查詢算出的距離；距離不是避難所的屬性）。要送出去當 JSON 的 `dict` 只在 `MapService`
產生，`current_people` → `current_ppl` 的改名也只發生在那一層。

這個改名，以及 `/api/shelters`、`/api/simulate_disaster`、`/api/nearest_shelter` 的欄位組合，
都是對外契約：前端的疏散動畫讀 `remaining` / `lat` / `lon`，收容人數回寫讀 `name`。
`tests/test_map_service.py` 把欄位組合釘死，欄位改名就不會讓地圖安靜地壞掉。

### 線上計數器與日誌

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
  只好把「最接近的其他資料」送給模型的比例。這個比例上升代表 `services/query_rules.py`
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
2026-10-05 08:59:12 INFO services.chat_service [e943c1f1]: chat route=rag source=generated outcome=ok context_chars=138 reply_chars=34 ms=12164
```

只有 `TRUST_PROXY_HEADERS=true` 時才會沿用上游帶進來的 `X-Request-ID`，而且必須符合
`[A-Za-z0-9._-]{1,64}`：這個值會進 log，不檢查等於讓任何人把換行塞進日誌。

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

CI 有三個 job：`ruff check .`、在乾淨的 Python 3.12 上從 lock 檔安裝後跑測試、以及
`docker build` 之後在**映像裡面**跑測試——最後這個才證明映像自己帶齊了依賴，
而且「不需要編譯器」的前提還成立。

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
│   ├── run_rag_eval.py         # 執行檢索評測
│   ├── metrics.py              # Recall / Precision / MRR
│   ├── run_gen_eval.py         # 執行生成評測（真實 ChatService + Ollama）
│   ├── gen_metrics.py          # 回答召回 / 幻覺 / 數字忠實 / 格式規則
│   └── results/                # 各次評測輸出
```

---

## 生成品質評測

檢索召回率只能說明「對的資料有沒有送到模型面前」。`evals/run_gen_eval.py` 量的是模型最後講出來的話：每一題都走真實的 `ChatService` 路徑（規則層、檢索、組 prompt、Ollama 生成），再用 `evals/gen_metrics.py` 的規則對回覆評分。不用另一個 LLM 當裁判，數字可以重現，也不受第二個模型影響。

```bash
docker exec -it disaster_app python evals/run_gen_eval.py --embedder ollama:bge-m3 --model llama3.2:3b --label gen_llama3.2-3b
docker exec -it disaster_app python evals/run_gen_eval.py --embedder ollama:bge-m3 --model qwen2.5:7b --label gen_qwen2.5-7b
docker exec -it disaster_app python evals/run_gen_eval.py --limit 10 --category region   # 快速抽查
```

題庫沿用同一份 130 題。`evals/build_eval_set.py` 會替每個正例附上 `expect`：各相關避難所的容量，以及「最大 / 最多」排名題應該排第一的那間。

| 指標 | 意義 |
|---|---|
| `ansR` 回答召回率 | 標準答案的避難所有多少比例真的在回覆裡被講出來（正式名稱或別名） |
| `ansP` 回答精確率 | 回覆裡講到的避難所有多少比例是相關的 |
| `full` | 回覆把所有相關避難所都講齊了 |
| `halluc` 幻覺 | 回覆講了檢索資料裡沒有的避難所，或出現對不上任何已知避難所的設施名稱 |
| `f_abst` 錯誤拒答 | 明明檢索到相關資料，回覆卻說沒有資料 |
| `num_ok` 數字忠實 | 回覆裡 ≥ 50 的整數都能在檢索資料裡找到（容量、收容、剩餘空間沒有被改寫或編造） |
| `cap_ok` | 單一避難所的題目，回覆有講出該避難所的容量 |
| `top1` | 排名題第一個講出來的避難所是正確的最大者 |
| `fmt_ok` 格式 | 只有繁體中文，沒有英文字母、簡體字、emoji |
| `trunc` 截斷 | Ollama 因 `num_predict` 用完而停止（清單被切掉） |
| 負例 `abstain_correct` | 範圍外或答不了的問題要拒答，而且不能講出任何避難所 |

每次執行也會記錄延遲 p50/p95、輸出 token 數，以及每題的完整回覆，存在 `evals/results/<label>.json`。

目前結果（八個正例類別的 macro 平均；`negatives` 是另外計算的拒答分數，`p50` 是每題端到端延遲中位數）：

| 設定 | ansR | ansP | full | halluc | f_abst | num_ok | fmt_ok | trunc | negatives | p50 |
|---|---|---|---|---|---|---|---|---|---|---|
| llama3.2:3b，本輪修改前的 prompt（baseline） | 0.725 | 0.719 | 0.707 | 0.022 | 0.214 | 0.986 | 1.000 | 0.014 | 0.900 | 9.4 秒 |
| **llama3.2:3b，目前的流程** | 0.961 | 0.913 | 0.943 | 0.006 | 0.016 | 0.994 | 1.000 | 0.000 | 1.000 | 8.6 秒 |
| **qwen2.5:7b，目前的流程** | 0.967 | 0.961 | 0.926 | 0.036 | 0.000 | 1.000 | 1.000 | 0.000 | 1.000 | 21.4 秒 |

baseline 最主要的問題不是幻覺，而是過度拒答：有 21% 的題目，檢索明明已經把被問到的避難所放進資料區，模型還是回「目前沒有相關資料」。三項修改把它消掉。

- 系統提示改成明講【避難所資料】是規則層事先篩好的，裡面列出的就必須拿來回答；只有資料區是空的、或問題根本與避難所無關時才能拒答。
- 帶地名的問句（「知本附近」「中華路一段附近」）改走檢索，不再被攔下來要座標。
- 直接點名某間避難所時，檢索只留那一筆，回答格式固定成每間一行。這兩項合起來讓 3B 模型的截斷率從 0.044 降到 0，它以前會把單一避難所的問題答成列十筆然後把輸出長度用完。

範圍外的問題現在由距離門檻擋下，不再交給模型判斷：純語意檢索最接近的文件超過 `RAG_MAX_DISTANCE` 就回固定句子，完全不進生成。天氣與補助類問題的 cosine 距離在 0.588 以上，而真的有答案的題目最遠是 0.483。

baseline 那一列的 `capacity_stated` 不能跟下面兩列比：這個欄位現在只套用在真的問容量或收容人數的題目，而不是每一道單一避難所的題目。

兩個模型剩下的錯誤都是把名稱寫漏或寫錯，不是編造事實：濤強國小寫成涙強國小、中興國小附幼簡寫成中興國小、台東縣立體育場寫成台東縣立體場。qwen2.5:7b 的 `halluc` 0.036 大部分就是這個，召回率差那一點也是同一個原因。兩個模型印出來的數字全部都能在檢索資料裡找到，沒有任何一題被截斷，十道範圍外問題全部正確拒答。3B 模型的 `top1` 0.667 是三道排名題裡有一道把第二大的避難所講在前面。

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
