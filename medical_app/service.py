"""Live index holder and minimal startup lifecycle (feat-005).

Holds the live :class:`medical_app.index.SearchIndex` in process memory so the
API layer (:mod:`medical_app.api`) can read search results via
:func:`get_live_index` and access the raw entry list / a freshness timestamp
via :func:`get_live_snapshot`.

Scope (feat-005 vs feat-006):
- **feat-005 owns** a trivial "build once at startup and store it" lifecycle:
  the FastAPI lifespan handler in :mod:`medical_app.api` loads entries, builds a
  :class:`SearchIndex`, wraps it in an immutable :class:`IndexSnapshot`
  alongside the entry list and an ``built_at`` timestamp, and installs it via
  :func:`set_live_snapshot`. Endpoints then read it. No scheduler, no reload,
  no atomic-swap machinery.
- **feat-006 will own** the atomic-swap live-index holder + reload scheduling +
  manual reload endpoint. When that lands, ``set_live_snapshot`` is expected to
  be replaced/extended with atomic-swap semantics (a single assignment of a
  freshly-built snapshot). The read API (:func:`get_live_index` /
  :func:`get_live_snapshot`) should stay stable so endpoints don't need
  rewriting — that is why this module exposes a snapshot rather than scattering
  three module-level globals.
- **feat-007 will own** env-driven config + structured logging + resilience on
  refresh (keeping the last good snapshot on reload failure). Today
  :func:`set_live_snapshot` is a plain assignment; on startup failure the
  lifespan handler is allowed to propagate the exception (feat-007 adds the
  fallback).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from medical_app.index import SearchIndex
from medical_app.models import MedicalEntry


@dataclass(frozen=True, slots=True)
class IndexSnapshot:
    """Immutable view of the live index plus its backing entries.

    Wrapping the index together with the raw entry list and a build timestamp
    lets the API serve both ``/search`` (via ``index``) and ``/entries`` /
    ``/entries/{id}`` / ``/health`` (via ``entries`` / ``built_at``) from one
    consistent object, without :class:`SearchIndex` having to expose its
    internals and without module-level globals drifting out of sync.

    The snapshot is frozen so a replacement (feat-006 atomic swap) is just a
    wholesale reference swap — readers either see the old snapshot or the new
    one, never a half-mixed state.

    Attributes:
        index: The :class:`SearchIndex` built from ``entries``.
        entries: The validated entries backing ``index`` (original field
            values preserved, in load order). Used for ``/entries`` paging and
            ``/entries/{id}`` lookup.
        built_at: UTC datetime the snapshot was built (freshness signal).
    """

    index: SearchIndex
    entries: list[MedicalEntry]
    built_at: datetime = field(default_factory=lambda: datetime.fromtimestamp(0.0))


def _empty_snapshot() -> IndexSnapshot:
    """Return a no-data snapshot used until the first successful startup load.

    Importing the package must be side-effect free, so the module-level holder
    starts empty rather than triggering a load. The lifespan handler replaces
    it with a real snapshot at startup.
    """
    return IndexSnapshot(index=SearchIndex([]), entries=[], built_at=datetime.now())


# Module-level holder for the live snapshot. feat-006 will wrap this in a
# small service object with atomic swap + scheduler; today it holds an empty
# snapshot so importing the package is side-effect free.
_live_snapshot: IndexSnapshot = _empty_snapshot()


def get_live_index() -> SearchIndex:
    """Return the :class:`SearchIndex` of the currently-live snapshot.

    Callers (the API search endpoint) read from this object. Equivalent to
    ``get_live_snapshot().index`` but kept as the stable, minimal read path the
    rest of the codebase already imports.
    """
    return _live_snapshot.index


def get_live_snapshot() -> IndexSnapshot:
    """Return the currently-live :class:`IndexSnapshot`.

    Endpoints that need the raw entry list or the build timestamp (``/entries``,
    ``/entries/{id}``, ``/health``) read from the snapshot rather than from the
    :class:`SearchIndex`, which does not expose its backing entries.
    """
    return _live_snapshot


def set_live_snapshot(snapshot: IndexSnapshot) -> None:
    """Install ``snapshot`` as the live snapshot (a single assignment).

    Used by the FastAPI lifespan handler at startup to publish the
    once-built index. feat-006 will replace this with atomic-swap + scheduler
    machinery; feat-007 will add keep-last-good-on-failure resilience. For
    feat-005 it is intentionally a plain assignment with no error handling —
    startup build failures are allowed to propagate to the lifespan handler.

    Args:
        snapshot: The snapshot to publish.
    """
    global _live_snapshot
    _live_snapshot = snapshot


__all__ = [
    "IndexSnapshot",
    "get_live_index",
    "get_live_snapshot",
    "set_live_snapshot",
]
