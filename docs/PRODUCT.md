# Product / Requirements

## Problem statement

The client provides a single API endpoint that dumps **all** their medical entries from a
database, as JSON. We must:

1. Build a **data pipeline** that loads this JSON dump.
2. Build an **API** that serves the data and supports **search**.

## Hard requirements

- **Typo tolerance in search.** Speech-to-text occasionally mis-transcribes locations and
  doctor names with simple typos, e.g. `Bucharest` → `Bukalest`, `Robert` → `Dobert`. The
  search API must return the correct entry despite these errors.
- **Fast responses.** Search latency should be low (sub-millisecond to low single-digit
  milliseconds at this data scale). See `docs/ARCHITECTURE.md` for how (in-memory index).
- **Low maintenance / continuous operation.** The system must keep running without ongoing
  manual intervention. This rules out solutions that require babysitting a second datastore
  or search server.

## Soft constraints / given facts

- **Data updates infrequently** — pulling the dump once a day is sufficient.
- The dataset is the client's *full* dump each time, so there is no incremental/delta API to
  design around; a full reload is the correct and simplest model.
- Data volume is assumed to fit comfortably in memory (entries, not blobs). If this assumption
  breaks, revisit the architecture decision in `docs/ARCHITECTURE.md`.

## Out of scope (unless explicitly requested)

- User authentication / authorization on the API.
- Writes/mutations — this is a read-only serving layer over the client's dump.
- A separate database or search server (Elasticsearch, Meilisearch, Postgres + pg_trgm).
  These are deliberately avoided to satisfy the low-maintenance requirement.
- Real-time streaming of the dump; a scheduled daily pull is enough.

## Mock data

Because the client endpoint is not yet available, we mock a representative JSON dump at
`data/mock_entries.json`. It is **synthetic** (no real PHI) and deliberately includes
transcription typos so fuzzy search can be exercised and tested. See `docs/DATA_SCHEMA.md`
for the entry shape.

## Success criteria

- `GET /search?q=Bukalest` returns the `Bucharest` entries (and `Dobert` returns `Robert`).
- `GET /entries` and `GET /entries/{id}` work.
- `GET /health` reports liveness and includes the index freshness timestamp.
- A daily reload swaps the index atomically; searches never observe partial data; a failed
  reload keeps serving the last good index.
- `./init.sh` passes end-to-end from a clean checkout.
