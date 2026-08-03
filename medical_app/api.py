"""FastAPI application: read + search endpoints + daily refresh (feat-006).

Exposes five endpoints over the in-memory medical-entry dataset:

- ``GET /health`` — liveness + index freshness timestamp + entry count.
- ``GET /entries`` — paginated list of all entries.
- ``GET /entries/{id}`` — a single entry by id (404 if unknown).
- ``GET /search?q=&field=&limit=`` — fuzzy, typo-tolerant search.
- ``POST /admin/reload`` — manually rebuild + atomically swap the live index.

The search index is built at startup (and rebuilt on each scheduled reload) in
a FastAPI ``lifespan`` handler, from ``data/mock_entries.json`` (via
:func:`medical_app.service.build_and_swap`), and held in process memory via
:mod:`medical_app.service` so searches are sub-millisecond. Endpoints read the
live snapshot via :func:`medical_app.service.get_live_snapshot` (entry list +
build timestamp) and :func:`medical_app.service.get_live_index` (search).

feat-006 additions:
- The lifespan now (a) builds the initial index via :func:`build_and_swap`, and
  (b) starts a daemon scheduler thread that re-calls :func:`build_and_swap` every
  ``settings.refresh_interval_seconds``. On shutdown the lifespan signals the
  thread to stop via a :class:`threading.Event` so no thread leaks.
- The scheduler swallows per-reload errors (logs + keeps the last good
  snapshot), so a single bad reload can't kill the background refresh loop.
- ``POST /admin/reload`` triggers an immediate :func:`build_and_swap` and
  returns a :class:`~medical_app.schemas.ReloadResponse` (always HTTP 200).

Scope (feat-006 vs feat-007):
- feat-007 will add env-driven config + structured logging polish + full
  resilience wiring. Today the scheduler logs reload failures and keeps serving
  the last good snapshot (the atomic-swap "don't swap on failure" guarantee),
  which is the minimum needed for the daily refresh to be safe.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from medical_app.config import settings
from medical_app.index import INDEXED_FIELDS
from medical_app.loader import LoaderError
from medical_app.models import MedicalEntry
from medical_app.schemas import (
    EntriesListResponse,
    EntryOut,
    ErrorResponse,
    HealthResponse,
    ReloadResponse,
    SearchHit,
    SearchResponse,
)
from medical_app.service import build_and_swap, get_live_snapshot

logger = logging.getLogger(__name__)

#: Maximum page size accepted by ``GET /entries``.
MAX_PAGE_SIZE: int = 200
#: Default page size for ``GET /entries`` when ``limit`` is omitted.
DEFAULT_PAGE_SIZE: int = 50
#: Maximum number of search hits returned by ``GET /search``.
MAX_SEARCH_LIMIT: int = 100

#: Module-level stop signal for the scheduler thread. The lifespan handler owns
#: setting/clearing it: set on shutdown so the daemon loop exits promptly. Kept
#: at module scope (rather than inline in the lifespan) so tests can assert the
#: scheduler is stopped after the lifespan exits.
_scheduler_stop: threading.Event = threading.Event()


def _run_scheduler(
    interval_seconds: int,
    data_path: str,
    stop_event: threading.Event,
) -> None:
    """Background loop that periodically reloads the live index.

    Sleeps ``interval_seconds`` between reload attempts and calls
    :func:`~medical_app.service.build_and_swap` to atomically refresh the live
    snapshot. Per-reload errors (e.g. a transiently-bad dump) are logged and
    swallowed so a single failure can't kill the scheduler thread — the live
    index keeps serving the last good snapshot (build_and_swap raises *before*
    touching the live reference, so a failure leaves it unchanged).

    Uses :meth:`threading.Event.wait` for the sleep so a shutdown signal wakes
    the thread immediately (a plain ``time.sleep`` would block until the next
    interval).

    Args:
        interval_seconds: Seconds to wait between reload attempts.
        data_path: Path to the JSON dump to reload from.
        stop_event: Event set by the lifespan on shutdown to stop the loop.
    """
    while not stop_event.wait(interval_seconds):
        try:
            build_and_swap(data_path)
        except LoaderError as exc:
            # Don't kill the scheduler on a bad reload: log and keep serving
            # the last good snapshot (build_and_swap guarantees no swap on fail).
            logger.warning("Scheduled reload of %s failed: %s", data_path, exc)
        except Exception:  # noqa: BLE001
            # Defensive: never let an unexpected error take down the scheduler.
            logger.exception(
                "Unexpected error during scheduled reload of %s", data_path
            )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Build the live index at startup and start the daily-refresh scheduler.

    Two things happen at startup:

    1. **Initial build**: call :func:`build_and_swap` to load
       ``settings.data_path``, build a :class:`SearchIndex`, and atomically
       publish it as the live snapshot. A startup build failure propagates
       (feat-007 will add keep-last-good wiring for startup; for feat-006 the
       app fails fast rather than serving an empty dataset silently).
    2. **Scheduler**: if ``settings.refresh_interval_seconds > 0``, start a
       daemon thread that calls :func:`build_and_swap` roughly every interval.
       The scheduler swallows per-reload errors so the thread stays alive.

    On shutdown the lifespan sets the module-level ``_scheduler_stop`` event so
    the scheduler thread exits promptly (no leaked daemon threads). The event is
    cleared on (re)startup so the lifespan can be re-entered cleanly under tests.

    Yields:
        Nothing; control returns to FastAPI to run the app, then the context
        exits on shutdown.
    """
    data_path = str(settings.data_path)

    # Initial build — publish the first live snapshot atomically.
    logger.info("Building search index from %s", data_path)
    snapshot = build_and_swap(data_path)
    logger.info(
        "Index ready: %d entries built at %s",
        len(snapshot.entries),
        snapshot.built_at.isoformat(),
    )

    # Start the scheduler (only if a positive interval is configured).
    _scheduler_stop.clear()
    interval = settings.refresh_interval_seconds
    scheduler_thread: threading.Thread | None = None
    if interval > 0:
        scheduler_thread = threading.Thread(
            target=_run_scheduler,
            args=(interval, data_path, _scheduler_stop),
            name="medical-app-refresh",
            daemon=True,
        )
        scheduler_thread.start()
        logger.info("Started refresh scheduler: reloading every %d seconds", interval)
    else:
        logger.info(
            "Refresh scheduler disabled (refresh_interval_seconds=%d)", interval
        )

    try:
        yield
    finally:
        # Signal the scheduler to stop on shutdown so no daemon thread leaks.
        _scheduler_stop.set()
        if scheduler_thread is not None:
            scheduler_thread.join(timeout=5.0)


app = FastAPI(
    title="Medical Entries Search API",
    description=(
        "Read and fuzzy-search a daily JSON dump of medical entries. "
        "See docs/ARCHITECTURE.md for the design overview."
    ),
    version="0.6.0",
    lifespan=lifespan,
)


def _entry_to_out(entry: MedicalEntry) -> EntryOut:
    """Project a domain :class:`MedicalEntry` to the API :class:`EntryOut`."""
    return EntryOut(
        id=entry.id,
        doctor_name=entry.doctor_name,
        location=entry.location,
        specialty=entry.specialty,
        facility=entry.facility,
        phone=entry.phone,
        notes=entry.notes,
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness probe + index freshness.

    Returns ``status == "ok"`` while the process is live, the ISO 8601 UTC
    timestamp the live index was built at, and the number of entries currently
    indexed.
    """
    snapshot = get_live_snapshot()
    return HealthResponse(
        status="ok",
        index_built_at=snapshot.built_at.isoformat(),
        entry_count=len(snapshot.entries),
    )


@app.get("/entries", response_model=EntriesListResponse)
def list_entries(
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
) -> EntriesListResponse:
    """Return a paginated slice of all entries.

    Query params:
        limit: Page size in ``[1, 200]`` (default 50). Out-of-range values are
            rejected with 422 by FastAPI.
        offset: Zero-based offset ``>= 0`` (default 0). An offset beyond the
            last entry yields an empty page with HTTP 200.

    The response echoes ``limit``/``offset`` and reports ``count`` (entries in
    this page) and ``total`` (entries in the whole dataset).
    """
    snapshot = get_live_snapshot()
    entries = snapshot.entries
    page = entries[offset : offset + limit]
    return EntriesListResponse(
        count=len(page),
        limit=limit,
        offset=offset,
        total=len(entries),
        entries=[_entry_to_out(e) for e in page],
    )


@app.get(
    "/entries/{entry_id}",
    response_model=EntryOut,
    responses={404: {"model": ErrorResponse}},
)
def get_entry(entry_id: str) -> EntryOut:
    """Return a single entry by id.

    Path params:
        entry_id: The entry ``id`` (e.g. ``ent-0001``).

    Raises:
        HTTPException: 404 if no entry has ``id == entry_id``.
    """
    snapshot = get_live_snapshot()
    for entry in snapshot.entries:
        if entry.id == entry_id:
            return _entry_to_out(entry)
    msg = f"entry {entry_id!r} not found"
    raise HTTPException(status_code=404, detail=msg)


@app.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., min_length=1, description="Search query (non-empty)."),
    field: str | None = Query(
        default=None,
        description=(
            f"Restrict matching to one indexed field: {', '.join(INDEXED_FIELDS)}."
        ),
    ),
    limit: int = Query(10, ge=1, le=MAX_SEARCH_LIMIT),
) -> SearchResponse:
    """Fuzzy, typo-tolerant search over indexed fields.

    Query params:
        q: Required non-empty query. An empty/whitespace string is rejected
            with 422 (``min_length=1``); a whitespace-only string is trimmed
            and, if empty afterwards, also surfaces as a 422.
        field: Optional indexed field to restrict matching to. Must be one of
            :data:`~medical_app.index.INDEXED_FIELDS`; anything else yields 422.
        limit: Max hits to return in ``[1, 100]`` (default 10).

    Ranking, thresholding and field-scoping are delegated to
    :meth:`~medical_app.index.SearchIndex.search`; results are mapped to
    :class:`~medical_app.schemas.SearchHit`.
    """
    if field is not None and field not in INDEXED_FIELDS:
        allowed = ", ".join(INDEXED_FIELDS)
        msg = f"unknown field {field!r}; expected one of: {allowed}"
        raise HTTPException(status_code=422, detail=msg)

    query = q.strip()
    if not query:
        msg = "query must be a non-empty string"
        raise HTTPException(status_code=422, detail=msg)

    snapshot = get_live_snapshot()
    results = snapshot.index.search(query, field=field, limit=limit)
    hits = [
        SearchHit(entry=_entry_to_out(r.entry), score=r.score, field=r.field)
        for r in results
    ]
    return SearchResponse(
        query=q,
        field=field,
        limit=limit,
        count=len(hits),
        hits=hits,
    )


@app.post("/admin/reload", response_model=ReloadResponse)
def reload() -> ReloadResponse:
    """Manually rebuild and atomically swap the live index.

    Calls :func:`~medical_app.service.build_and_swap` against
    ``settings.data_path`` to rebuild the index fully off to the side and then
    atomically publish it. Always responds with HTTP 200 so the caller gets a
    clean JSON body:

    - **Success**: ``{reloaded: true, entry_count, built_at, skipped, total}``
      where ``built_at`` is the new snapshot's ISO 8601 UTC build timestamp and
      ``entry_count`` is the number of entries now live.
    - **Failure**: ``{reloaded: false, entry_count: 0, built_at: "", error: str}``.
      The live index is left untouched — the last good snapshot keeps serving
      (the atomic-swap "don't swap on failure" guarantee). The most common
      failure is a :class:`~medical_app.loader.LoaderError` (missing file,
      malformed JSON, non-array top level).
    """
    data_path = str(settings.data_path)
    try:
        snapshot = build_and_swap(data_path)
    except LoaderError as exc:
        logger.warning("Manual reload of %s failed: %s", data_path, exc)
        return ReloadResponse(reloaded=False, error=str(exc))
    return ReloadResponse(
        reloaded=True,
        entry_count=len(snapshot.entries),
        built_at=snapshot.built_at.isoformat(),
        skipped=snapshot.skipped,
        total=snapshot.total,
    )


__all__ = ["app", "get_entry", "health", "list_entries", "reload", "search"]
