"""In-memory fuzzy search index over loaded medical entries (feat-004).

Builds a typo-tolerant search index on top of the entries produced by
:func:`medical_app.loader.load_entries`, using :mod:`rapidfuzz` for
Levenshtein-based similarity scoring. The index is built once at startup
(and rebuilt on each reload — feat-006 swaps the live reference atomically);
searches are pure in-memory scoring and run in well under a millisecond at
this dataset's scale (~tens to low hundreds of entries).

Design:
- **Per-field storage.** For each entry we store the lowercased value of each
  indexed field (``doctor_name``, ``location``, ``specialty``) rather than a
  single concatenated blob. This lets a field-scoped query
  (``search(q, field="location")``) score against just that field, and lets a
  cross-field query take the *best* per-field score per entry — so a strong
  hit on one field isn't dragged down by a weak hit on another.
- **Scorer.** We score each (query, field-value) pair with two complementary
  :mod:`rapidfuzz` scorers and take the maximum:
    * ``fuzz.partial_ratio`` — best alignment of the shorter string inside the
      longer one. Great for substring/single-token typos, e.g. ``"dobert"``
      finding ``"robert ionescu"`` (the ``robert`` token inside the name).
    * ``fuzz.token_sort_ratio`` — order-independent full-string ratio after
      sorting tokens. Great for transposition/spacing typos where the whole
      field is one word, e.g. ``"bukalest"`` vs ``"bucharest"`` (a 1-edit
      substitution that ``partial_ratio`` under-scores).
  ``max`` of the two handles both families well; ``WRatio`` alone is a decent
  general-purpose composite but loses the substring strength of
  ``partial_ratio`` for the ``Dobert`` case, so the explicit max is preferred.
- **Cheap rebuild.** Building the index is O(N): we only lowercase and store
  the field strings. No O(N^2) precompute. All scoring happens at search time
  (O(N * fields) per query), which is fast at this scale and keeps rebuilds
  cheap for the daily refresh (feat-006).
- **Ranking.** Results are sorted by score descending; ties are broken
  deterministically by entry ``id`` ascending so the same query always yields
  the same order.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rapidfuzz import fuzz

from medical_app.models import MedicalEntry

DEFAULT_THRESHOLD: float = 70.0
"""Default minimum similarity score (0-100) to keep a hit.

Chosen so the canonical typo pair ``"Bukalest"`` -> ``"Bucharest"`` (which
scores ~70.6) is captured while clearly unrelated pairs stay well below.
Overridable per :class:`SearchIndex` (constructor) and per :meth:`search` call.
"""

DEFAULT_LIMIT: int = 10
"""Default maximum number of results returned by :meth:`search`."""

INDEXED_FIELDS: tuple[str, ...] = ("doctor_name", "location", "specialty")
"""Fields indexed for search, in field-restriction order.

``doctor_name`` and ``location`` are the primary typo-prone targets per
``docs/DATA_SCHEMA.md``; ``specialty`` is included for filtering. ``specialty``
is optional on the model and simply contributes nothing when ``None``.
"""


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A single ranked search hit.

    Attributes:
        entry: The matched :class:`MedicalEntry` (original field values,
            unchanged, for API responses).
        score: Similarity score in the inclusive range ``[0.0, 100.0]``.
            Higher is better. For a cross-field search this is the best score
            across the indexed fields; for a field-scoped search it is the
            score against just that field.
        field: The indexed field that produced ``score`` (one of
            :data:`INDEXED_FIELDS`), or ``None`` if the query did not match
            any field above the threshold (results below threshold are not
            returned, so in practice this is always set on returned results).
    """

    entry: MedicalEntry
    score: float
    field: str | None = None


class SearchIndex:
    """In-memory typo-tolerant search index over medical entries.

    Constructed from the loader's validated entries (e.g.
    ``SearchIndex(load_entries(path).entries)``). The index stores lowercased
    per-field strings; :meth:`search` scores at query time.

    The instance is effectively immutable after construction (the stored
    entries/fields are not mutated), which makes the feat-006 atomic-swap
    pattern straightforward: the service holds a ``SearchIndex`` reference and
    replaces it wholesale on reload rather than mutating it in place.

    Args:
        entries: The validated entries to index (typically
            ``LoadResult.entries``). Order is preserved as a tie-break key.
        threshold: Default minimum similarity score (0-100) for
            :meth:`search` when the caller does not pass one. Defaults to
            :data:`DEFAULT_THRESHOLD`.
    """

    def __init__(
        self,
        entries: Sequence[MedicalEntry],
        *,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> None:
        """Build the index by storing lowercased per-field text for each entry.

        O(N) in the number of entries: we only lowercase and stash the field
        values. No pairwise precompute.
        """
        self._entries: list[MedicalEntry] = list(entries)
        # Parallel list of dicts: per-entry lowercased value for each indexed
        # field. Missing (None) optional fields are stored as None and skipped
        # at search time so they never produce a score.
        self._fields: list[dict[str, str | None]] = [
            {
                "doctor_name": e.doctor_name.lower(),
                "location": e.location.lower(),
                "specialty": e.specialty.lower() if e.specialty is not None else None,
            }
            for e in self._entries
        ]
        self._default_threshold: float = float(threshold)

    @property
    def default_threshold(self) -> float:
        """The minimum similarity score used when a search omits ``threshold``."""
        return self._default_threshold

    @property
    def size(self) -> int:
        """Number of entries currently in the index."""
        return len(self._entries)

    def search(
        self,
        query: str,
        *,
        field: str | None = None,
        limit: int = DEFAULT_LIMIT,
        threshold: float | None = None,
    ) -> list[SearchResult]:
        """Return ranked, thresholded fuzzy matches for ``query``.

        Args:
            query: The search term. Normalized (stripped + lowercased) before
                matching. An empty/whitespace query yields an empty list
                without scoring anything.
            field: Restrict matching to a single indexed field (one of
                :data:`INDEXED_FIELDS`). If ``None`` (default), every indexed
                field is scored and the best score per entry wins. Passing an
                unknown field name raises :class:`ValueError`.
            limit: Maximum number of results to return. Must be >= 0; clamped
                to the number of available hits. Defaults to
                :data:`DEFAULT_LIMIT`.
            threshold: Minimum similarity score (0-100) to keep a hit for this
                call. If ``None``, falls back to the index's
                :attr:`default_threshold`.

        Returns:
            Up to ``limit`` :class:`SearchResult` objects sorted by ``score``
            descending, ties broken by entry ``id`` ascending. Each result's
            ``field`` reports which indexed field produced the score (for a
            field-scoped search this is always ``field``).

        Raises:
            ValueError: If ``field`` is not one of :data:`INDEXED_FIELDS`, or
                if ``limit`` is negative.
        """
        normalized_query = query.strip().lower()
        if not normalized_query:
            return []

        if limit < 0:
            msg = f"limit must be non-negative, got {limit}"
            raise ValueError(msg)

        if field is not None and field not in INDEXED_FIELDS:
            allowed = ", ".join(INDEXED_FIELDS)
            msg = f"unknown field {field!r}; expected one of: {allowed}"
            raise ValueError(msg)

        effective_threshold = (
            self._default_threshold if threshold is None else float(threshold)
        )

        fields_to_search: tuple[str, ...]
        if field is None:
            fields_to_search = INDEXED_FIELDS
        else:
            fields_to_search = (field,)

        results: list[SearchResult] = []
        for entry, field_values in zip(self._entries, self._fields, strict=True):
            best_score = -1.0
            best_field: str | None = None
            for fname in fields_to_search:
                value = field_values[fname]
                if value is None:
                    continue
                score = _score(normalized_query, value)
                if score > best_score:
                    best_score = score
                    best_field = fname
            if best_field is not None and best_score >= effective_threshold:
                results.append(
                    SearchResult(entry=entry, score=best_score, field=best_field)
                )

        # Rank by score descending; deterministic tie-break by id ascending.
        results.sort(key=lambda r: (-r.score, r.entry.id))

        if limit == 0:
            return []
        return results[:limit]


def _score(query: str, value: str) -> float:
    """Return the best similarity score (0-100) for ``query`` vs ``value``.

    Uses the maximum of ``fuzz.partial_ratio`` (substring/single-token typos)
    and ``fuzz.token_sort_ratio`` (full-field transposition/spacing typos) so
    both families of real-world transcription errors are captured. Both inputs
    are assumed already lowercased/trimmed by the caller.
    """
    return max(fuzz.partial_ratio(query, value), fuzz.token_sort_ratio(query, value))


__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_THRESHOLD",
    "INDEXED_FIELDS",
    "SearchIndex",
    "SearchResult",
]
