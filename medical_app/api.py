"""FastAPI application: read + search endpoints (feat-005).

Exposes four endpoints over the in-memory medical-entry dataset:

- ``GET /health`` — liveness + index freshness timestamp + entry count.
- ``GET /entries`` — paginated list of all entries.
- ``GET /entries/{id}`` — a single entry by id (404 if unknown).
- ``GET /search?q=&field=&limit=`` — fuzzy, typo-tolerant search.

The search index is built **once** at startup, in a FastAPI ``lifespan`` handler,
from ``data/mock_entries.json`` (via :func:`medical_app.loader.load_entries` and
:class:`medical_app.index.SearchIndex`), and held in process memory via
:mod:`medical_app.service` so searches are sub-millisecond. Endpoints read the
live snapshot via :func:`medical_app.service.get_live_snapshot` (entry list +
build timestamp) and :func:`medical_app.service.get_live_index` (search).

Scope (feat-005 vs feat-006/007):
- feat-006 will add the atomic-swap reload + scheduler + manual reload endpoint;
  the lifespan handler here is the single startup build point feat-006 will
  extend. The read endpoints should not need changes.
- feat-007 will add env-driven config + structured logging + resilience on
  refresh. Today startup build failures propagate out of the lifespan (the app
  fails fast, which is acceptable for feat-005).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query

from medical_app.config import settings
from medical_app.index import INDEXED_FIELDS, SearchIndex
from medical_app.loader import load_entries
from medical_app.models import MedicalEntry
from medical_app.schemas import (
    EntriesListResponse,
    EntryOut,
    ErrorResponse,
    HealthResponse,
    SearchHit,
    SearchResponse,
)
from medical_app.service import IndexSnapshot, get_live_snapshot, set_live_snapshot

logger = logging.getLogger(__name__)

#: Maximum page size accepted by ``GET /entries``.
MAX_PAGE_SIZE: int = 200
#: Default page size for ``GET /entries`` when ``limit`` is omitted.
DEFAULT_PAGE_SIZE: int = 50
#: Maximum number of search hits returned by ``GET /search``.
MAX_SEARCH_LIMIT: int = 100


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Build the live index once at startup and publish it via the service.

    Loads entries from ``settings.data_path``, builds a
    :class:`~medical_app.index.SearchIndex`, wraps it (plus the entry list and
    an ISO-typed build timestamp) in an immutable
    :class:`~medical_app.service.IndexSnapshot`, and installs it via
    :func:`~medical_app.service.set_live_snapshot`. After startup, endpoints
    read from the snapshot; nothing is mutated at request time.

    feat-006 will hook the reload/scheduler here; feat-007 will add resilience
    (keep last good on failure). For feat-005, a startup build failure is
    allowed to propagate — the app fails fast rather than serving an empty
    dataset silently.

    Yields:
        Nothing; control returns to FastAPI to run the app, then the context
        exits on shutdown.
    """
    data_path = Path(settings.data_path)
    logger.info("Building search index from %s", data_path)
    load_result = load_entries(data_path)
    index = SearchIndex(load_result.entries)
    built_at = datetime.now(UTC)
    snapshot = IndexSnapshot(
        index=index,
        entries=load_result.entries,
        built_at=built_at,
    )
    set_live_snapshot(snapshot)
    logger.info(
        "Index ready: %d entries (%d skipped of %d) built at %s",
        len(load_result.entries),
        load_result.skipped,
        load_result.total,
        built_at.isoformat(),
    )
    yield


app = FastAPI(
    title="Medical Entries Search API",
    description=(
        "Read and fuzzy-search a daily JSON dump of medical entries. "
        "See docs/ARCHITECTURE.md for the design overview."
    ),
    version="0.5.0",
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


__all__ = ["app", "get_entry", "health", "list_entries", "search"]
