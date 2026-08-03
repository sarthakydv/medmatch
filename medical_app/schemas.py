"""Pydantic v2 request/response schemas for the FastAPI API (feat-005).

These models define the *serialized* shape of the API, decoupled from the
domain model (:class:`medical_app.models.MedicalEntry`) and the search result
:class:`medical_app.index.SearchResult`. Keeping them separate means the
internal models can evolve without breaking the public API contract, and lets
FastAPI generate clean OpenAPI docs from the response_model annotations on
each route in :mod:`medical_app.api`.

Design notes:
- ``EntryOut`` mirrors the authoritative fields from
  :class:`medical_app.models.MedicalEntry` (required ``id``, ``doctor_name``,
  ``location``; the rest optional). Optional fields use ``None`` defaults.
- ``SearchHit`` wraps an :class:`EntryOut` plus the ``score`` and the indexed
  ``field`` that produced it, matching :class:`medical_app.index.SearchResult`.
- ``SearchResponse`` / ``EntriesListResponse`` echo the request parameters
  (``query``/``field``/``limit`` and ``limit``/``offset``) plus a ``count`` so
  callers can confirm paging and result-set size without a separate call.
- ``HealthResponse`` reports liveness plus an ISO 8601 ``index_built_at``
  timestamp captured at startup and the total ``entry_count``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class EntryOut(BaseModel):
    """Serialized form of a single medical entry (API response shape).

    Mirrors the authoritative fields of
    :class:`medical_app.models.MedicalEntry`. Required: ``id``,
    ``doctor_name``, ``location``. The rest are optional and default to
    ``None`` when absent on the source entry.
    """

    id: str = Field(..., description="Stable unique id of the entry.")
    doctor_name: str = Field(..., description="Doctor name (may contain typos).")
    location: str = Field(..., description="Practice location (may contain typos).")
    specialty: str | None = Field(
        default=None, description="Medical specialty, if known."
    )
    facility: str | None = Field(
        default=None, description="Facility / clinic name, if known."
    )
    phone: str | None = Field(default=None, description="Contact phone, if known.")
    notes: str | None = Field(default=None, description="Free-text notes, if any.")


class SearchHit(BaseModel):
    """One ranked fuzzy-search hit.

    Attributes:
        entry: The matched entry (original field values).
        score: Similarity score in ``[0.0, 100.0]``; higher is better.
        field: The indexed field (``doctor_name`` / ``location`` /
            ``specialty``) that produced ``score``. ``None`` only if no field
            cleared the threshold (not returned in practice).
    """

    entry: EntryOut
    score: float = Field(..., description="Similarity score in [0.0, 100.0].")
    field: str | None = Field(
        default=None,
        description="Indexed field that produced the score, if any.",
    )


class SearchResponse(BaseModel):
    """Envelope for ``GET /search``.

    Echoes the request (``query``, ``field``, ``limit``) and reports the
    number of returned ``hits``.
    """

    query: str = Field(..., description="The original query string.")
    field: str | None = Field(
        default=None,
        description="The field restriction passed, if any.",
    )
    limit: int = Field(..., description="The effective result cap.")
    count: int = Field(..., description="Number of hits returned in this response.")
    hits: list[SearchHit] = Field(
        default_factory=list,
        description="Ranked, thresholded search hits (best first).",
    )


class EntriesListResponse(BaseModel):
    """Paginated envelope for ``GET /entries``.

    Attributes:
        count: Number of entries returned in this page (``len(entries)``).
        limit: The effective page size (echoed from the request / default).
        offset: The zero-based offset used for this page (echoed).
        total: Total number of entries in the loaded dataset (across all
            pages). Useful for paging UIs; unaffected by ``limit``/``offset``.
        entries: The entries in this page (the sliced view).
    """

    count: int = Field(..., description="Number of entries returned in this page.")
    limit: int = Field(..., description="Effective page size (echoed).")
    offset: int = Field(..., description="Zero-based offset used (echoed).")
    total: int = Field(..., description="Total entries in the loaded dataset.")
    entries: list[EntryOut] = Field(
        default_factory=list,
        description="Entries in this page.",
    )


class HealthResponse(BaseModel):
    """Liveness + readiness payload for ``GET /health``.

    ``status`` is ``"ok"`` while the process is live; ``index_built_at`` is the
    ISO 8601 timestamp captured when the in-memory search index was built at
    startup (a simple freshness signal — full resilience wiring is feat-007);
    ``entry_count`` is the number of entries currently indexed.
    """

    status: str = Field(..., description='Liveness status; "ok" when live.')
    index_built_at: str = Field(
        ...,
        description="ISO 8601 UTC timestamp the live index was built at.",
    )
    entry_count: int = Field(..., description="Number of entries in the live index.")


class ErrorResponse(BaseModel):
    """Error body for non-2xx responses (e.g. 404 not found)."""

    detail: str = Field(..., description="Human-readable error detail.")


class ReloadResponse(BaseModel):
    """Outcome payload for ``POST /admin/reload`` (feat-006 manual reload).

    The endpoint always responds with HTTP 200 so callers get a clean JSON body
    whether the reload succeeded or failed; the ``reloaded`` boolean is the
    authoritative signal. On success the body carries the new snapshot's
    summary fields; on failure it carries an ``error`` string and the live
    index is left untouched (the last good snapshot keeps serving).

    Attributes:
        reloaded: ``True`` if a fresh snapshot was built and published; ``False``
            if the reload failed and the live index is unchanged.
        entry_count: Number of entries in the new snapshot (0 on failure).
        built_at: ISO 8601 UTC timestamp the new snapshot was built at (empty
            string on failure).
        skipped: Number of entries dropped during the load (validation failures
            / duplicate ids). 0 on failure.
        total: Total raw records read from the dump. 0 on failure.
        error: Human-readable error detail when ``reloaded`` is ``False``;
            ``None`` on success.
    """

    reloaded: bool = Field(..., description="True if the reload published a new index.")
    entry_count: int = Field(
        default=0, description="Entries in the new snapshot (0 on failure)."
    )
    built_at: str = Field(
        default="",
        description=(
            "ISO 8601 UTC build timestamp of the new snapshot (empty on failure)."
        ),
    )
    skipped: int = Field(default=0, description="Entries skipped during the load.")
    total: int = Field(default=0, description="Total raw records read from the dump.")
    error: str | None = Field(
        default=None,
        description="Error detail when reloaded is False; None on success.",
    )


__all__ = [
    "EntriesListResponse",
    "EntryOut",
    "ErrorResponse",
    "HealthResponse",
    "ReloadResponse",
    "SearchHit",
    "SearchResponse",
]
