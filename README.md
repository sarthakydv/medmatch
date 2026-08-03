# Medical Entries Data Pipeline & Search API

A single FastAPI service that loads a JSON dump of medical entries, builds an
in-memory **typo-tolerant** search index ([rapidfuzz](https://github.com/maxbachmann/RapidFuzz)),
and serves read/search endpoints. No database, no external search server —
the whole index lives in process memory and refreshes atomically once a day.

> **Headline demo:** `GET /search?q=Bukalest` returns the `Bucharest` entries,
> and `GET /search?q=Dobert` returns the `Robert` entries — despite the typos.

---

## Architecture

A single Python service (FastAPI) owns three things:

1. **Loader** — reads + validates the JSON dump into typed entries.
2. **Index** — an in-memory, typo-tolerant search structure over those entries.
3. **API** — a thin HTTP layer that reads from the in-memory index.

```
┌────────────────────┐   daily    ┌──────────────┐    atomically    ┌──────────────┐
│  Client JSON dump  │ ─────────▶ │  Loader      │ ───────────────▶ │  Live Index  │
│  (mock for now)    │   pull     │  (validate,  │   swap ref       │  (in-memory, │
└────────────────────┘            │   normalize) │                  │   rapidfuzz) │
                                  └──────────────┘                  └──────┬───────┘
                                                                           │ read-only
                                                                           ▼
                                                                  ┌────────────────┐
                                                                  │  FastAPI app   │
                                                                  │  /search ...   │
                                                                  └────────────────┘
```

The loader builds a *new* index fully off to the side, then the service
**atomically swaps** the live reference (a single assignment). Concurrent
in-flight searches keep running against the old index until they finish; new
searches hit the new one — no request ever sees a half-loaded dataset. A daemon
thread re-pulls the dump once a day (configurable); if a reload fails, the
service **keeps serving the last good index** and logs the error rather than
crashing or publishing a bad one.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design, and
[`docs/PRODUCT.md`](docs/PRODUCT.md) / [`docs/DATA_SCHEMA.md`](docs/DATA_SCHEMA.md)
for the requirements and entry shape.

---

## Prerequisites

- **Python 3.11+** (for the local run), and optionally **Docker** (for the
  container run).
- That's it — there is no database or search server to provision.

---

## Quick start (local)

```bash
git clone <repo-url> medical_app
cd medical_app
./init.sh
```

`./init.sh` creates a `.venv`, installs runtime + dev dependencies, and runs
the full verification suite (`compileall`, `pytest`, `ruff`, `mypy`). A fresh
checkout passes with no manual steps.

Activate the venv and start the API:

```bash
source .venv/bin/activate
python -m medical_app.main          # respects all MEDICAL_* env vars
# (equivalently: uvicorn medical_app.api:app --host 0.0.0.0 --port 8000)
```

The API now serves on **http://localhost:8000**. Try it:

```bash
curl http://localhost:8000/health
```

## Quick start (Docker)

```bash
docker compose up --build        # builds the image and serves on :8000
```

That's the whole low-maintenance deployment story: a multi-stage slim image,
non-root user, healthcheck, and `restart: unless-stopped`. See
[`docker-compose.yml`](docker-compose.yml) for the knobs (you can mount
`./data` to supply your own dump without rebuilding).

---

## Configuration

All settings are env-driven (prefix `MEDICAL_`) and have sensible defaults, so
the service runs on a clean machine with no env vars and no `.env` file.

| Variable | Default | Purpose |
|---|---|---|
| `MEDICAL_DATA_PATH` | `data/mock_entries.json` | Path to the JSON dump to load + index |
| `MEDICAL_REFRESH_INTERVAL_SECONDS` | `86400` (24h) | Seconds between scheduled reloads (`0` disables the scheduler) |
| `MEDICAL_HOST` | `0.0.0.0` | API bind host |
| `MEDICAL_PORT` | `8000` | API bind port (1–65535) |
| `MEDICAL_LOG_LEVEL` | `INFO` | Root log level: `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` (case-insensitive) |
| `MEDICAL_FUZZY_THRESHOLD` | `70.0` | Default minimum fuzzy-match similarity score (0–100) for the search index |

`.env` example (optional — env vars and defaults are always sufficient):

```dotenv
MEDICAL_DATA_PATH=data/mock_entries.json
MEDICAL_REFRESH_INTERVAL_SECONDS=86400
MEDICAL_HOST=0.0.0.0
MEDICAL_PORT=8000
MEDICAL_LOG_LEVEL=INFO
MEDICAL_FUZZY_THRESHOLD=70.0
```

For Docker, these are set in [`docker-compose.yml`](docker-compose.yml) under the
`api` service `environment:` block.

---

## API endpoints

| Method & path | Description |
|---|---|
| `GET /health` | Liveness probe: status, the index build timestamp, and entry count |
| `GET /entries?limit=&offset=` | Paginated list of all entries (`limit` 1–200, default 50; `offset` ≥ 0) |
| `GET /entries/{id}` | A single entry by id (e.g. `ent-0001`); `404` if unknown |
| `GET /search?q=&field=&limit=` | Fuzzy, typo-tolerant search over `doctor_name`/`location`/`specialty` |
| `POST /admin/reload` | Manually rebuild + atomically swap the live index |

Interactive docs are auto-generated at **http://localhost:8000/docs** (OpenAPI).

---

## Triggering a reload

The daily scheduler reloads automatically, but you can force an immediate
rebuild + swap at any time:

```bash
curl -X POST http://localhost:8000/admin/reload
```

The endpoint always returns **HTTP 200** with a clean JSON body, so the
`reloaded` boolean is the authoritative signal. On success it rebuilds the
index fully off to the side and atomically publishes it (no request ever sees
a half-loaded dataset); on failure the live index is left untouched and the
last good snapshot keeps serving.

```json
{
  "reloaded": true,
  "entry_count": 73,
  "built_at": "2026-08-03T10:00:00.000000+00:00",
  "skipped": 0,
  "total": 73,
  "error": null
}
```

A failed reload looks like `{ "reloaded": false, "entry_count": 0, "built_at": "", "skipped": 0, "total": 0, "error": "..." }`.

---

## Example queries

### Fuzzy search — the headline demo

The typos baked into the synthetic data make these the core product demos:

```bash
# Typo "Bukalest" -> finds "Bucharest" entries despite the 1-edit substitution.
curl 'http://localhost:8000/search?q=Bukalest'
```

```json
{
  "query": "Bukalest",
  "field": null,
  "limit": 10,
  "count": 10,
  "hits": [
    {
      "entry": {
        "id": "ent-0001",
        "doctor_name": "Dobert Pop",
        "location": "Bukalest",
        "specialty": "Cardiology",
        "facility": "Central Clinic",
        "phone": "+40 700 000 001",
        "notes": "Follow-up in two weeks."
      },
      "score": 100.0,
      "field": "location"
    },
    { "entry": { "id": "ent-0006", "doctor_name": "Robert-Adrian Vasile", "location": "Bukalest", "specialty": "Pediatrics", "facility": "Children's Clinic", "phone": "+40 700 000 004", "notes": "Vaccination records on file." }, "score": 100.0, "field": "location" },
    { "entry": { "id": "ent-0007", "doctor_name": "Maria Popescu", "location": "Bukarest", "specialty": "Gynecology", "facility": "Women's Health Clinic", "phone": "+40 700 000 005", "notes": "Routine checkup." }, "score": 87.5, "field": "location" },
    { "entry": { "id": "ent-0028", "doctor_name": "Mihai Serban", "location": "Budapest", "specialty": "Gastroenterology", "facility": "Danube Gastro Clinic", "phone": "+36 700 000 016", "notes": "Endoscopy booked." }, "score": 75.0, "field": "location" },
    { "entry": { "id": "ent-0002", "doctor_name": "Dobert Marin", "location": "Bucharest", "specialty": "Cardiology", "facility": null, "phone": null, "notes": null }, "score": 70.59, "field": "location" },
    { "entry": { "id": "ent-0003", "doctor_name": "Robert Ionescu", "location": "Bucharest", "specialty": "Neurology", "facility": "University Hospital", "phone": "+40 700 000 002", "notes": "Family history of migraines." }, "score": 70.59, "field": "location" }
  ]
}
```

(The 4 exact `Bukalest` matches score `100.0`; `Bukarest`/`Budapest` score `87.5`/`75.0`;
the genuinely-correct `Bucharest` entries still clear the default `70.0` threshold at
`~70.59` — so the typo query recovers them. 4 further hits are truncated here.)

```bash
# Typo "Dobert" -> finds "Robert"/"Roberto" doctor names.
curl 'http://localhost:8000/search?q=Dobert'
```

```bash
# Restrict matching to a single indexed field.
curl 'http://localhost:8000/search?q=Cluj&field=location&limit=5'
```

### Lookups + health

```bash
curl 'http://localhost:8000/entries/ent-0001'
curl 'http://localhost:8000/health'
curl 'http://localhost:8000/entries?limit=5&offset=0'
```

`GET /health`:

```json
{
  "status": "ok",
  "index_built_at": "2026-08-03T10:00:00.000000+00:00",
  "entry_count": 73
}
```

`GET /entries/{id}`:

```json
{
  "id": "ent-0001",
  "doctor_name": "Dobert Pop",
  "location": "Bukalest",
  "specialty": "Cardiology",
  "facility": "Central Clinic",
  "phone": "+40 700 000 001",
  "notes": "Follow-up in two weeks."
}
```

`GET /entries?limit=5&offset=0`:

```json
{
  "count": 5,
  "limit": 5,
  "offset": 0,
  "total": 73,
  "entries": [ { "id": "ent-0001", "...": "..." } ]
}
```

---

## Running tests / checks

```bash
./init.sh            # everything (creates venv, installs deps, runs all checks)
# or, individually:
source .venv/bin/activate
pytest
ruff check .
mypy medical_app
```

---

## Project layout

```
medical_app/
  __init__.py
  config.py          # env-driven settings (MEDICAL_*)
  models.py          # Pydantic entry model
  loader.py          # read + validate + normalize the JSON dump
  index.py           # in-memory rapidfuzz search index
  service.py         # live index holder + atomic swap + reload scheduling
  api.py             # FastAPI app: /health, /entries, /entries/{id}, /search, /admin/reload
  logging_config.py  # structured logging setup
  main.py            # uvicorn entrypoint
tests/               # pytest suite (loader, index, api, models, refresh, config, smoke)
docs/                # ARCHITECTURE.md, PRODUCT.md, DATA_SCHEMA.md
data/                # synthetic dump (see Data note below)
Dockerfile           # multi-stage slim build
docker-compose.yml   # local Docker run
init.sh              # verify/run-cleanly entrypoint
```

---

## Data note

Only the **synthetic** file `data/mock_entries.json` (73 entries, deliberately
including typos like `Bukalest`/`Dobert`) is tracked in the repo — it contains
no real PHI. Real client data lives in `data/raw/` (gitignored) and is pointed
to at runtime via `MEDICAL_DATA_PATH`. For Docker, mount `./data` (already wired
in `docker-compose.yml`) and set `MEDICAL_DATA_PATH` to the file you want the
container to load.
