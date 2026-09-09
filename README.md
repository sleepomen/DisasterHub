# Disaster Hub

<div align="center">

[![English](https://img.shields.io/badge/English-README-1f6feb?style=for-the-badge)](README.md)
[![繁體中文](https://img.shields.io/badge/%E7%B9%81%E9%AB%94%E4%B8%AD%E6%96%87-README-9e9e9e?style=for-the-badge)](README.zh-TW.md)

</div>

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

Optional variables: `OLLAMA_MODEL` (default `llama3.2:3b`), `OLLAMA_TEMPERATURE`, `OLLAMA_NUM_PREDICT`, `OLLAMA_TIMEOUT`, `EMBEDDING_PROVIDER` (`ollama` or `minilm`), `EMBEDDING_MODEL` (default `bge-m3`), `EMBEDDING_TIMEOUT`, `RAG_TOP_K`, `DB_POOL_MIN` / `DB_POOL_MAX`.

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

If required environment variables (database credentials and the like) are missing at startup, the service exits with a clear error message instead of running with a broken configuration.

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
| POST | `/api/chat` | AI decision assistant |
| POST | `/api/sync` | Trigger a data sync manually (requires the `X-API-Key` header) |

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

  The app reads `X-Forwarded-Proto` to decide whether to mark the cookie `Secure`; Caddy and nginx send that header by default.
- If your organisation already has SSO or a VPN, put the whole domain behind it and treat the in-app login as a second layer.
- Set `SESSION_SECRET` to a fixed long random string, otherwise every restart logs everyone out.
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
