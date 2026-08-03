"""Live index holder with atomic swap + reload primitive (feat-006).

Holds the live :class:`medical_app.index.SearchIndex` in process memory so the
API layer (:mod:`medical_app.api`) can read search results via
:func:`get_live_index` and access the raw entry list / a freshness timestamp
via :func:`get_live_snapshot`.

Scope (feat-005 vs feat-006 vs feat-007):
- **feat-005 owned** a trivial "build once at startup and store it" lifecycle.
- **feat-006 owns** (this module) the atomic-swap live-index holder + the
  reusable :func:`build_and_swap` reload primitive. The FastAPI lifespan handler
  in :mod:`medical_app.api` now also starts a scheduler thread that periodically
  calls :func:`build_and_swap`, and a ``POST /admin/reload`` endpoint exposes a
  manual reload. The read API (:func:`get_live_index` / :func:`get_live_snapshot`)
  stays stable so endpoints need no rewriting.
- **feat-007 will own** env-driven config + structured logging + full resilience
  polish. feat-006 already implements the core "don't swap on failure" guarantee
  (a reload that raises never touches the live snapshot), which feat-007 will
  build on with better logging.

Concurrency model (why readers never see partial data):

- The live index is a single module-level reference, ``_live_snapshot``.
- :class:`IndexSnapshot` is frozen + slotted, so a snapshot is *immutable* once
  built — there is no in-place mutation of its ``index``, ``entries`` or
  ``built_at``. A refresh constructs a brand-new snapshot off to the side and
  only the final handoff mutates module state.
- That handoff is a single assignment ``_live_snapshot = new_snapshot``. Under
  CPython, a single attribute assignment to a module global is atomic with
  respect to the GIL: any reader either observes the old reference or the new
  one, never a half-written pointer. Because each reference points at a fully
  formed, immutable snapshot, a reader therefore sees a *fully consistent* old
  or new dataset — never a mix.
- A module-level :class:`threading.Lock` (``_swap_lock``) serializes *reload
  attempts* so two overlapping reloads don't both build+swap. Critically, the
  lock is held only for the instant of the assignment: the expensive load + index
  construction happen *before* the lock is acquired, so concurrent searches are
  never blocked.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from medical_app.config import settings
from medical_app.index import SearchIndex
from medical_app.loader import LoadResult, load_entries
from medical_app.models import MedicalEntry

logger = logging.getLogger(__name__)


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
        built_at: Timezone-aware UTC datetime the snapshot was built (freshness
            signal).
        skipped: Number of records dropped during the load (validation failures
            / duplicate ids). Diagnostic metadata from the build; ``0`` for a
            clean load and for the empty startup placeholder.
        total: Total raw records read from the dump when the snapshot was built
            (``len(entries) + skipped``). ``0`` for the empty placeholder.
    """

    index: SearchIndex
    entries: list[MedicalEntry]
    built_at: datetime = field(default_factory=lambda: datetime.fromtimestamp(0.0))
    skipped: int = 0
    total: int = 0


def _empty_snapshot() -> IndexSnapshot:
    """Return a no-data snapshot used until the first successful startup load.

    Importing the package must be side-effect free, so the module-level holder
    starts empty rather than triggering a load. The lifespan handler replaces
    it with a real snapshot at startup.
    """
    return IndexSnapshot(index=SearchIndex([]), entries=[], built_at=datetime.now(UTC))


# Module-level holder for the live snapshot. A single assignment to this global
# is the atomic swap (see module docstring for the concurrency reasoning).
_live_snapshot: IndexSnapshot = _empty_snapshot()

#: Serializes overlapping reload attempts so only one build+swap runs at a time.
#: Held only for the instant of the swap (load + index build happen *before* the
#: lock is acquired), so concurrent searches are never blocked by a reload.
_swap_lock = threading.Lock()


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
    """Install ``snapshot`` as the live snapshot (atomic, single assignment).

    Used by the FastAPI lifespan handler at startup to publish the once-built
    index, and by :func:`build_and_swap` to publish a freshly-built reload.
    Routes the assignment through ``_swap_lock`` so a startup install and a
    concurrent reload can't interleave their swaps.

    Args:
        snapshot: The snapshot to publish.
    """
    global _live_snapshot
    with _swap_lock:
        _live_snapshot = snapshot


def build_and_swap(data_path: str | Path) -> IndexSnapshot:
    """Build a fresh snapshot from ``data_path`` and atomically publish it.

    The core feat-006 primitive, used by the scheduler, the manual reload
    endpoint, and (transitively) any future CLI reload command. Steps:

    1. **Load** the entries from ``data_path`` via
       :func:`medical_app.loader.load_entries`. Structural problems (missing
       file, malformed JSON, non-array top level) raise :class:`LoaderError`;
       individually-bad entries are skipped and counted by the loader.
    2. **Build** a new :class:`SearchIndex` from the loaded entries and wrap it
       — plus the entries and a fresh UTC ``built_at`` — in an immutable
       :class:`IndexSnapshot`.
    3. **Swap**: acquire ``_swap_lock`` and assign the new snapshot to
       ``_live_snapshot`` (a single, atomic reference swap). The lock is held
       only for the assignment.

    Steps 1 and 2 run *outside* the lock, so concurrent in-flight searches are
    unaffected while the new snapshot is being constructed — they keep reading
    the old snapshot object. Only the instant of the swap is serialized, and
    because the snapshot is immutable and the swap is a single assignment,
    readers see either the fully-old or the fully-new snapshot, never partial.

    Resilience on failure: if step 1 or 2 raises (bad dump, validation error,
    :class:`LoaderError`), the live snapshot is **not touched** — the service
    keeps serving the last good snapshot. The exception propagates to the
    caller (the scheduler logs + swallows it; the endpoint reports it).

    Args:
        data_path: Path to the JSON dump to (re)load.

    Returns:
        The newly-built and now-live :class:`IndexSnapshot`.

    Raises:
        LoaderError: If the dump file is missing/unreadable, not valid JSON, or
            its top-level value is not a JSON array.
    """
    path = Path(data_path)
    new_snapshot = _build_snapshot(path)
    # Only the swap is serialized; construction happened above, outside the lock.
    with _swap_lock:
        global _live_snapshot
        _live_snapshot = new_snapshot
    logger.info(
        "Reloaded live index from %s: %d entries, built at %s",
        path,
        len(new_snapshot.entries),
        new_snapshot.built_at.isoformat(),
    )
    return new_snapshot


def _build_snapshot(path: Path) -> IndexSnapshot:
    """Load ``path`` and construct (but do NOT publish) a new snapshot.

    Kept separate from :func:`build_and_swap` so all the failure-prone work
    (file read, parse, validation, index construction) completes *before* the
    caller acquires the swap lock. If anything here raises, no module state has
    changed.

    Args:
        path: Path to the JSON dump.

    Returns:
        A brand-new, immutable :class:`IndexSnapshot` (not yet installed).

    Raises:
        LoaderError: Propagated from :func:`load_entries` on structural errors.
    """
    load_result: LoadResult = load_entries(path)
    index = SearchIndex(load_result.entries, threshold=settings.fuzzy_threshold)
    return IndexSnapshot(
        index=index,
        entries=load_result.entries,
        built_at=datetime.now(UTC),
        skipped=load_result.skipped,
        total=load_result.total,
    )


__all__ = [
    "IndexSnapshot",
    "build_and_swap",
    "get_live_index",
    "get_live_snapshot",
    "set_live_snapshot",
]
