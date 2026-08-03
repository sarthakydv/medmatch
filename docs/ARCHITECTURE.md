# Architecture

## Overview

A single Python service (FastAPI) owns three things:

1. **Loader** — reads + validates the JSON dump into typed entries.
2. **Index** — an in-memory, typo-tolerant search structure over those entries.
3. **API** — thin HTTP layer that reads from the in-memory index.

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

## Key decisions

### Python 3.11+ / FastAPI
FastAPI gives async I/O, automatic request/response validation via Pydantic, and a small
surface area — well suited to a read-heavy, low-maintenance service.

### No database
The client hands us the complete dataset as a daily dump, and it fits in memory. Adding a
database would introduce a second failure surface, backups, and schema migrations — all of
which work against the "continuous, low-maintenance" requirement. We keep the index in
process memory.

### Typo tolerance via `rapidfuzz` (Levenshtein), in-memory
The described transcription errors are simple typos with edit distance ~1–2. `rapidfuzz`
provides very fast Levenshtein/ratio scoring in pure compute (no second process to run or
keep healthy), which matches the low-maintenance goal better than a search server. We index
the searchable text fields (doctor name, location, and optionally specialty) and rank hits by
similarity score, filtering by a configurable threshold.

Alternatives considered and rejected:
- **Elasticsearch / Meilisearch** — powerful but require running and maintaining a separate
  service, indices, and disks. Overkill for a small, daily-replaced dataset.
- **Postgres + pg_trgm** — adds a database (rejected above) for a benefit we don't need.

### Atomic index swap for the daily refresh
The loader builds a *new* index fully off to the side, then the service atomically swaps the
live reference (a single assignment of a module-level / app-state pointer). Concurrent
in-flight searches continue against the old index object until they finish; new searches hit
the new one. This guarantees no request ever sees a half-loaded dataset.

### Resilience on refresh failure
If a daily reload fails (bad dump, network, validation error), the service **keeps serving
the last good index** and logs the error. It never replaces a good index with a bad one.

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `MEDICAL_DATA_PATH` | `data/mock_entries.json` | Path to the JSON dump |
| `MEDICAL_REFRESH_INTERVAL_SECONDS` | `86400` (24h) | How often to re-pull/reload |
| `MEDICAL_HOST` | `0.0.0.0` | API host |
| `MEDICAL_PORT` | `8000` | API port |
| `MEDICAL_LOG_LEVEL` | `INFO` | Log level |
| `MEDICAL_FUZZY_THRESHOLD` | (per-field, tuned) | Minimum similarity score to return a hit |

## Package layout (target)

```
medical_app/
  __init__.py
  config.py          # env-driven settings
  models.py          # Pydantic entry model (see docs/DATA_SCHEMA.md)
  loader.py          # read + validate + normalize JSON dump
  index.py           # in-memory rapidfuzz search index
  service.py         # the live index holder + atomic swap + reload scheduling
  api.py             # FastAPI app: /health, /entries, /entries/{id}, /search
  main.py            # uvicorn entrypoint
tests/
  test_loader.py
  test_index.py
  test_api.py
```

## Performance notes

- Index is built once at startup and on each reload; searches are pure in-memory scoring.
- At the expected scale, searches are sub-millisecond to low single-digit milliseconds.
- If the dataset ever outgrows memory, the architecture decision (no DB / in-memory) must be
  revisited here — do not silently switch approaches.
