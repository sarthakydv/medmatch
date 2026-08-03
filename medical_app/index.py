"""In-memory fuzzy search index (rapidfuzz).

This is a minimal stub for ``feat-001``. The full implementation lands in
``feat-004``: build a typo-tolerant index over the loaded entries using
rapidfuzz for Levenshtein-based matching across ``doctor_name`` and
``location`` (plus ``specialty`` for filtering), ranked by similarity score
with a configurable threshold. The index must rebuild quickly so the daily
refresh is cheap.
"""

from __future__ import annotations

from collections.abc import Sequence

from medical_app.models import MedicalEntry


class SearchIndex:
    """Placeholder search index.

    ``feat-004`` will store the entries and precomputed searchable text and
    implement :meth:`search` returning ranked, thresholded results.
    """

    def __init__(self, entries: Sequence[MedicalEntry]) -> None:
        """Store entries for later indexing.

        ``feat-004`` precomputes searchable text fields here.
        """
        self._entries: Sequence[MedicalEntry] = list(entries)

    def search(
        self,
        query: str,
        *,
        field: str | None = None,
        limit: int = 10,
    ) -> list[MedicalEntry]:
        """Return matching entries ranked by similarity.

        Returns an empty list today; ``feat-004`` implements real scoring.
        """
        _ = (query, field, limit)
        return []


__all__ = ["SearchIndex"]
