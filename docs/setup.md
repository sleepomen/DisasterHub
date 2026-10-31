# Setup and development

[← README](../README.md) · English · [繁體中文](zh-TW/setup.md)

## Quick Start

### Requirements

- Docker Desktop
- [Ollama](https://ollama.com) installed on the host (containers reach it via `host.docker.internal`)
- 16 GB RAM or more

### 1. Clone the project

```bash
git clone https://github.com/sleepomen/DisasterHub.git
cd DisasterHub
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
| `ALERT_POLL_SECONDS` / `ALERT_TTL_MINUTES` | How often live debris-flow alerts are fetched (default 600 s) and how long a fetched alert stays in effect if later fetches fail (default 180 min). `ALERT_POLL_SECONDS=0` disables fetching, for offline machines; the tests set it to 0 |
| `DB_CONNECT_TIMEOUT` / `DB_STATEMENT_TIMEOUT_MS` | Database connect timeout (seconds, default 5) and per-statement timeout (milliseconds, default 15000), so a hung database fails requests within seconds instead of blocking every worker thread |

Optional variables: `OLLAMA_MODEL` (default `llama3.2:3b`), `OLLAMA_TEMPERATURE`, `OLLAMA_NUM_CTX` (default 8192 — must hold the 20–30 shelter documents a list-style query retrieves), `OLLAMA_NUM_PREDICT` (default 800), `OLLAMA_TIMEOUT`, `EMBEDDING_PROVIDER` (`ollama` or `minilm`), `EMBEDDING_MODEL` (default `bge-m3`), `EMBEDDING_TIMEOUT`, `RAG_TOP_K`, `RAG_MAX_DISTANCE` (default 0.70 — cosine distance above which a plain semantic search is treated as "no relevant data"; a last-resort backstop, since off-topic questions are already refused by the earlier layers), `KNOWLEDGE_MAX_DISTANCE` (default 0.45 — the same cutoff for knowledge passages), `DB_POOL_MIN` / `DB_POOL_MAX` (default 1 / 10).

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

CI runs three jobs: `ruff check .`; the tests on a clean Python 3.12 installed from the lock file,
followed by the routing evaluation as a regression gate (`--min-accuracy 1.0`); and `docker build`
followed by the tests *inside* the image — the last one is what proves the image carries everything it
needs and that the "no compiler required" assumption still holds.

## Deployment notes

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
