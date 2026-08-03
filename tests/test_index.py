"""Unit tests for :mod:`medical_app.index` (feat-004 in-memory fuzzy search).

Covers the contract from ``docs/ARCHITECTURE.md`` and ``feature_list.json``:

- **Critical typo tolerance**: searching ``"Bukalest"`` surfaces entries whose
  correct location is ``Bucharest``; searching ``"Dobert"`` surfaces entries
  whose correct ``doctor_name`` is ``Robert ...``. These are built from the
  real ``data/mock_entries.json`` via :func:`load_entries`.
- **Ranking**: exact matches score highest; results are sorted by score
  descending; ties are broken deterministically.
- **Threshold filtering**: raising the threshold shrinks the result set.
- **Field restriction**: ``search(q, field="location")`` only matches the
  location field, never ``doctor_name``.
- **Limit**: the result count is capped.
- **Determinism**: the same query twice yields the same order.
- **Empty/whitespace query**: returns an empty list without crashing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from medical_app.index import (
    DEFAULT_LIMIT,
    DEFAULT_THRESHOLD,
    INDEXED_FIELDS,
    SearchIndex,
    SearchResult,
)
from medical_app.loader import load_entries
from medical_app.models import MedicalEntry

REPO_ROOT = Path(__file__).resolve().parent.parent
MOCK_ENTRIES_PATH = REPO_ROOT / "data" / "mock_entries.json"


# --- Helpers -----------------------------------------------------------------


def _entry(
    eid: str,
    doctor_name: str,
    location: str,
    *,
    specialty: str | None = None,
) -> MedicalEntry:
    """Build a minimal :class:`MedicalEntry` for hand-built test fixtures."""
    return MedicalEntry(
        id=eid,
        doctor_name=doctor_name,
        location=location,
        specialty=specialty,
    )


def _real_index() -> SearchIndex:
    """Build a :class:`SearchIndex` from the real synthetic dump."""
    return SearchIndex(load_entries(MOCK_ENTRIES_PATH).entries)


# --- Critical typo tolerance (spec-required assertions) ----------------------


def test_bukalest_matches_bucharest() -> None:
    """Searching the typo ``"Bukalest"`` surfaces entries located in Bucharest.

    The dataset contains both ``"Bukalest"`` (typo) and ``"Bucharest"``
    (correct) locations. Fuzzy search must bridge the typo: at least one entry
    whose *correct* location is ``Bucharest`` appears among the top results.
    """
    index = _real_index()
    results = index.search("Bukalest", limit=20)

    assert len(results) > 0, "expected at least one hit for 'Bukalest'"

    # Every result must carry a score and a populated field.
    assert all(isinstance(r, SearchResult) for r in results)
    assert all(r.field is not None for r in results)

    # The strongest hits must be location-field matches (the query is a city).
    top_fields = {r.field for r in results[: min(5, len(results))]}
    assert "location" in top_fields

    # The crux: a Bucharest-located entry is returned (fuzzy bridges the typo).
    bucharest_hits = [r for r in results if r.entry.location == "Bucharest"]
    assert bucharest_hits, (
        "fuzzy search should surface Bucharest entries for the 'Bukalest' typo"
    )
    best_bucharest = max(bucharest_hits, key=lambda r: r.score)
    assert best_bucharest.field == "location"
    assert best_bucharest.score >= DEFAULT_THRESHOLD


def test_dobert_matches_robert() -> None:
    """Searching the typo ``"Dobert"`` surfaces entries whose doctor is Robert.

    The dataset contains both ``"Dobert ..."`` (typo) and ``"Robert ..."``
    (correct) doctor names. Fuzzy search must surface correct ``Robert``
    entries (within ~1 edit distance of the query), proving the typo is
    bridged — not just exact ``Dobert`` matches.
    """
    index = _real_index()
    results = index.search("Dobert", limit=20)

    assert len(results) > 0, "expected at least one hit for 'Dobert'"

    # The crux: at least one returned entry has a correct "Robert ..." name
    # (a word starting with "Robert"), not just the typo'd "Dobert" ones.
    robert_hits = [
        r for r in results if r.entry.doctor_name.lower().startswith("robert")
    ]
    assert robert_hits, (
        "fuzzy search should surface correct 'Robert' entries for the "
        "'Dobert' typo, not just exact 'Dobert' matches"
    )

    # The matched Robert entry should be ranked via the doctor_name field.
    best_robert = max(robert_hits, key=lambda r: r.score)
    assert best_robert.field == "doctor_name"
    assert best_robert.score >= DEFAULT_THRESHOLD

    # And the literal Dobert entries must rank at least as high (they're
    # substring-exact for the query token), confirming sane ordering.
    dobert_hits = [
        r for r in results if r.entry.doctor_name.lower().startswith("dobert")
    ]
    assert dobert_hits, "the literal typo entries should also be present"
    best_dobert = max(dobert_hits, key=lambda r: r.score)
    assert best_dobert.score >= best_robert.score


def test_typo_pairs_score_above_default_threshold() -> None:
    """The canonical typo pairs clear the default threshold on the real data.

    A generous limit is used because the exact typo variants (``Bukalest``)
    outrank the bridged correct variants (``Bucharest``) and would otherwise
    consume the top slots before the correct-form entry appears.
    """
    index = _real_index()

    bucharest_results = index.search("Bukalest", limit=30)
    bucharest = next(r for r in bucharest_results if r.entry.location == "Bucharest")
    assert bucharest.score >= DEFAULT_THRESHOLD

    robert_results = index.search("Dobert", limit=30)
    robert = next(
        r for r in robert_results if r.entry.doctor_name.lower().startswith("robert")
    )
    assert robert.score >= DEFAULT_THRESHOLD


# --- Ranking -----------------------------------------------------------------


def test_exact_match_scores_highest() -> None:
    """An exact field match scores the maximum (100) and ranks first."""
    entries = [
        _entry("a", "Robert Pop", "Bucharest"),
        _entry("b", "Roberto", "Cluj"),
        _entry("c", "Robert Ionescu", "Iasi"),
    ]
    index = SearchIndex(entries)
    results = index.search("Robert Pop", threshold=0)

    assert results, "expected at least one hit"
    top = results[0]
    assert top.entry.id == "a"
    assert top.score == pytest.approx(100.0)
    # The top score must be >= every other score.
    assert all(top.score >= r.score for r in results)


def test_results_sorted_by_score_descending() -> None:
    """Result scores are non-increasing from first to last."""
    index = _real_index()
    results = index.search("Bucharest", threshold=0, limit=15)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_ties_broken_deterministically_by_id() -> None:
    """Equal scores are broken by entry ``id`` ascending, deterministically.

    Two entries with identical doctor_name produce identical scores; the one
    with the lexicographically smaller id must come first, and re-searching
    must yield the same order.
    """
    entries = [
        _entry("zzz", "Robert", "X"),
        _entry("aaa", "Robert", "Y"),
        _entry("mmm", "Robert", "Z"),
    ]
    index = SearchIndex(entries)
    first = index.search("Robert", threshold=0)
    second = index.search("Robert", threshold=0)

    # All three score the same against doctor_name "robert".
    assert [r.entry.id for r in first] == ["aaa", "mmm", "zzz"]
    assert [r.entry.id for r in first] == [r.entry.id for r in second]


# --- Threshold filtering -----------------------------------------------------


def test_threshold_filters_results() -> None:
    """A higher threshold returns a subset of (or equal to) a lower threshold."""
    index = _real_index()
    loose = index.search("Robert", threshold=0, limit=50)
    strict = index.search("Robert", threshold=90, limit=50)

    assert len(loose) >= len(strict)
    assert all(r.score >= 90 for r in strict)


def test_threshold_too_high_returns_empty() -> None:
    """A threshold above 100 (impossible score) returns nothing."""
    entries = [_entry("a", "Robert", "Bucharest")]
    index = SearchIndex(entries)
    assert index.search("Robert", threshold=100.1) == []


def test_search_uses_index_default_threshold() -> None:
    """Omitting ``threshold`` falls back to the index's configured default."""
    entries = [
        _entry("a", "Robert", "Bucharest"),  # exact match -> 100
        _entry("b", "Robart", "X"),  # 1-edit typo -> partial ~85, below 95
    ]
    high = SearchIndex(entries, threshold=95)
    results = high.search("Robert")
    # Only the exact match clears 95; the 1-edit typo does not.
    assert [r.entry.id for r in results] == ["a"]


# --- Field restriction -------------------------------------------------------


def test_field_restriction_location_only() -> None:
    """``field="location"`` matches location, never ``doctor_name``.

    Entry ``a`` would match via ``doctor_name`` in a cross-field search, but
    with ``field="location"`` it must be absent (its location ``"Cluj-Napoca"``
    scores far below the default threshold for the query ``"Bucharest"``).
    """
    entries = [
        # doctor_name contains the query token, location does NOT.
        _entry("a", "Bucharest Clinic", "Cluj-Napoca"),
        # location contains the query token.
        _entry("b", "Someone Else", "Bucharest"),
    ]
    index = SearchIndex(entries)

    # Field-scoped: only entry b's location matches.
    scoped = index.search("Bucharest", field="location")
    scoped_ids = {r.entry.id for r in scoped}
    assert "b" in scoped_ids
    assert "a" not in scoped_ids
    assert all(r.field == "location" for r in scoped)

    # Cross-field control: entry a IS returned when no field is pinned, because
    # its doctor_name matches. This proves the restriction above was meaningful.
    cross = index.search("Bucharest")
    cross_ids = {r.entry.id for r in cross}
    assert "a" in cross_ids


def test_field_restriction_doctor_name_only() -> None:
    """``field="doctor_name"`` matches doctor_name, never location.

    Entry ``b``'s location ``"Robert Town"`` would match in a cross-field
    search, but with ``field="doctor_name"`` it must be absent (its doctor_name
    ``"Other Doc"`` scores far below the default threshold).
    """
    entries = [
        _entry("a", "Robert Pop", "Robertsville"),
        _entry("b", "Other Doc", "Robert Town"),
    ]
    index = SearchIndex(entries)

    scoped = index.search("Robert", field="doctor_name")
    scoped_ids = {r.entry.id for r in scoped}
    assert "a" in scoped_ids
    assert "b" not in scoped_ids
    assert all(r.field == "doctor_name" for r in scoped)

    # Cross-field control: entry b IS returned via its location.
    cross = index.search("Robert")
    cross_ids = {r.entry.id for r in cross}
    assert "b" in cross_ids


def test_cross_field_takes_best_score() -> None:
    """Without ``field``, the best per-field score wins for each entry."""
    entries = [
        # Weak doctor_name hit, strong location hit.
        _entry("a", "X", "Bucharest"),
    ]
    index = SearchIndex(entries)
    results = index.search("Bucharest", threshold=0)
    assert len(results) == 1
    # The strong location match should make this a near-perfect hit.
    assert results[0].score == pytest.approx(100.0)
    assert results[0].field == "location"


def test_unknown_field_raises_value_error() -> None:
    """An unrecognized field name raises ``ValueError``."""
    index = SearchIndex([_entry("a", "Dr", "X")])
    with pytest.raises(ValueError):
        index.search("Dr", field="phone")  # phone is not an indexed field


def test_indexed_fields_constant_is_complete() -> None:
    """The public ``INDEXED_FIELDS`` constant names the searchable fields."""
    assert INDEXED_FIELDS == ("doctor_name", "location", "specialty")


# --- Limit -------------------------------------------------------------------


def test_limit_is_respected() -> None:
    """The result count never exceeds ``limit``."""
    entries = [_entry(f"id{i:02d}", "Robert", "X") for i in range(15)]
    index = SearchIndex(entries)

    assert len(index.search("Robert", threshold=0, limit=5)) == 5
    assert len(index.search("Robert", threshold=0, limit=1)) == 1
    assert len(index.search("Robert", threshold=0, limit=3)) == 3


def test_limit_zero_returns_empty() -> None:
    """``limit=0`` returns an empty list even when matches exist."""
    entries = [_entry("a", "Robert", "Bucharest")]
    index = SearchIndex(entries)
    assert index.search("Robert", limit=0) == []


def test_default_limit_is_ten() -> None:
    """When ``limit`` is omitted, at most :data:`DEFAULT_LIMIT` results return."""
    assert DEFAULT_LIMIT == 10
    entries = [_entry(f"id{i:02d}", "Robert", "X") for i in range(25)]
    index = SearchIndex(entries)
    results = index.search("Robert", threshold=0)
    assert len(results) == DEFAULT_LIMIT


def test_negative_limit_raises_value_error() -> None:
    """A negative ``limit`` is rejected."""
    index = SearchIndex([_entry("a", "Dr", "X")])
    with pytest.raises(ValueError):
        index.search("Dr", limit=-1)


# --- Empty / whitespace query ------------------------------------------------


@pytest.mark.parametrize("query", ["", "   ", "\t", "\n  \n"])
def test_empty_or_whitespace_query_returns_empty(query: str) -> None:
    """An empty/whitespace query yields an empty list and never crashes."""
    index = _real_index()
    assert index.search(query) == []


def test_empty_query_does_not_raise_on_empty_index() -> None:
    """An empty index plus an empty query is a safe no-op."""
    index = SearchIndex([])
    assert index.search("") == []
    assert index.search("anything") == []


# --- Construction / metadata -------------------------------------------------


def test_size_reflects_entry_count() -> None:
    """``size`` reports the number of indexed entries."""
    entries = [
        _entry("a", "Dr A", "X"),
        _entry("b", "Dr B", "Y"),
    ]
    assert SearchIndex(entries).size == 2
    assert SearchIndex([]).size == 0


def test_specialty_field_is_searchable_when_present() -> None:
    """``specialty`` is indexed and searchable when set on the entry."""
    entries = [
        _entry("a", "Dr A", "X", specialty="Cardiology"),
        _entry("b", "Dr B", "Y", specialty="Pediatrics"),
    ]
    index = SearchIndex(entries)
    # Default threshold excludes the dissimilar Dermatology entry.
    results = index.search("Cardiology", field="specialty")
    assert [r.entry.id for r in results] == ["a"]
    assert results[0].field == "specialty"


def test_none_specialty_does_not_match() -> None:
    """A ``None`` specialty contributes no score (never spuriously matches)."""
    entries = [_entry("a", "Dr A", "X")]  # specialty is None
    index = SearchIndex(entries)
    results = index.search("anything", field="specialty", threshold=0)
    assert results == []


def test_original_field_values_preserved_in_results() -> None:
    """Returned entries keep their original (non-lowercased) field values."""
    entries = [_entry("a", "Robert Pop", "Bucharest")]
    index = SearchIndex(entries)
    results = index.search("Robert", threshold=0)
    assert len(results) == 1
    assert results[0].entry.doctor_name == "Robert Pop"
    assert results[0].entry.location == "Bucharest"
