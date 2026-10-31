# API and observability

[← README](../README.md) · English · [繁體中文](zh-TW/api.md)

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
| POST | `/api/simulate_disaster` | Run the spatial disaster simulation; returns the affected shelters, estimates of the population inside the radius, the evacuation demand and the shelter shortfall, the evacuation plan (`evacuation`, with each flow's road route) and the resulting occupancy, which is already written to the database. `mode` is `walk` (default) or `drive` (requires login, or `X-API-Key`) |
| POST | `/api/occupancy` | Set each shelter's current occupancy directly (for external tools or manual correction; the simulation no longer needs it); only the changed vector documents are recomputed (requires login, or `X-API-Key`) |
| POST | `/api/reset_simulation` | Clear the simulation state and restore occupancy to the initial values from the source data (requires login, or `X-API-Key`) |
| POST | `/api/nearest_shelter` | Find the nearest shelters, ranked by travel time (`mode`: `walk` or `drive`). Each result has `road_distance_km` (`null` when it cannot be routed), the straight-line `distance_km`, `walk_min`, `drive_min`, `hazard_m` (metres still inside an alert zone) and `unreachable` (cut off by a red alert) |
| GET | `/api/hazard_zones` | Debris-flow impact zones as GeoJSON, for the map layer |
| GET | `/api/alerts` | Debris-flow alerts in effect, with the length of road each one closes or reroutes |
| POST | `/api/chat` | AI decision assistant. Streams the answer back as Server-Sent Events. Returns 429 when all `CHAT_MAX_CONCURRENT` generation slots are busy |
| POST | `/api/sync` | Trigger a data sync manually. Accepts `SYNC_API_KEY`, a logged-in session, or `WRITE_API_KEY` — the startup sync can fail, and an operator must be able to recover without restarting the container. The index is rebuilt into a staging collection and swapped in atomically, so chat keeps answering from the old index while embeddings are recomputed |

`/health/ready` treats the database and the vector index as hard requirements — restarting the container can recover them. Ollama is only reported, never decisive, because it runs on the host and restarting the container cannot fix it. The container `HEALTHCHECK` hits this endpoint. Readiness also stays answerable while Ollama is slow: query and document embeddings are computed outside the index lock, so a stalled embedding call cannot block the `count()` the index check performs. If the sync at startup fails the service still starts and retries it in the background with backoff (5 s, 15 s, 30 s, 60 s, 120 s, 300 s) until it succeeds — a database that was not up yet, or an Ollama still loading the embedding model, fixes itself inside that window; until then readiness stays 503.

Manual sync example:

```bash
curl -X POST http://localhost:8501/api/sync -H "X-API-Key: $SYNC_API_KEY"
```

## Streaming the Chat Answer

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

## Online Counters and Logs

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
  means the keyword tables in `services/chat/query_rules.py` are mis-reading real questions.
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
2026-10-05 08:59:12 INFO services.chat.service [e943c1f1]: chat route=rag source=generated outcome=ok context_chars=138 reply_chars=34 ms=12164
```

An incoming `X-Request-ID` is only honoured when `TRUST_PROXY_HEADERS=true`, and only if it matches
`[A-Za-z0-9._-]{1,64}` — the value reaches the log, so an unchecked one would let anyone inject newlines
into it.
