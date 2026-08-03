"""Live index holder with atomic swap and reload scheduling.

This is a minimal stub for ``feat-001``. The full implementation lands in
``feat-006``: hold the live :class:`medical_app.index.SearchIndex` in process
memory, build a *new* index off to the side on each reload, then atomically
swap the live reference (a single assignment) so concurrent searches never
see a half-loaded dataset. On reload failure, keep serving the last good
index and log the error rather than crashing (resilience, feat-007).
"""

from __future__ import annotations

from medical_app.index import SearchIndex

# Module-level holder for the live index. ``feat-006`` will wrap this in a
# small service object with atomic swap + scheduler; today it holds an empty
# index so importing the package is side-effect free.
_live_index: SearchIndex = SearchIndex([])


def get_live_index() -> SearchIndex:
    """Return the currently-live search index.

    Callers (the API layer) read from this object. ``feat-006`` swaps it
    atomically on reload.
    """
    return _live_index


__all__ = ["get_live_index"]
