"""FastAPI application: read + search endpoints.

This is a minimal stub for ``feat-001``. The full implementation lands in
``feat-005``: ``GET /health``, ``GET /entries`` (paginated list),
``GET /entries/{id}`` (single entry), and ``GET /search?q=&field=&limit=``
(fuzzy search). The index is built once at startup and held in process
memory so searches are sub-millisecond.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(
    title="Medical Entries Search API",
    description=(
        "Read and fuzzy-search a daily JSON dump of medical entries. "
        "See docs/ARCHITECTURE.md for the design overview."
    ),
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe. ``feat-005`` adds readiness tied to the index."""
    return {"status": "ok"}


__all__ = ["app", "health"]
