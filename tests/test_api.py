"""Tests for :mod:`medical_app.api` (feat-005 FastAPI read + search endpoints).

Covers the contract from ``docs/PRODUCT.md`` and ``feature_list.json``:

- ``GET /health`` reports ``status == "ok"``, a valid ISO ``index_built_at``, and
  ``entry_count > 0`` (the lifespan builds the index from the real
  ``data/mock_entries.json``).
- ``GET /entries`` paginates: returns up to ``limit`` entries, echoes
  ``limit``/``offset``/``total``/``count``, and ``offset`` beyond the end yields
  an empty page with HTTP 200.
- ``GET /entries/{id}`` returns the entry for a known id and 404 for unknown.
- ``GET /search?q=Bukalest`` returns Bucharest entries (the critical
  typo-bridging assertion), and ``?q=Dobert`` returns ``Robert...`` doctors.
- ``GET /search`` validates ``q`` (required, non-empty) and ``field`` (one of
  the indexed fields), and ``field=location`` restricts matching to location.

Uses :class:`fastapi.testclient.TestClient`, which triggers the app's lifespan
handler (so the index is built from the default data path with no manual init).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from medical_app.api import app
from medical_app.index import INDEXED_FIELDS


@pytest.fixture(scope="module")
def client() -> TestClient:
    """A :class:`TestClient` whose lifespan has built the index once.

    ``scope="module"`` builds the client (and thus the index) a single time for
    the whole module; the index is immutable after startup so reusing the
    client across tests is safe and keeps the suite fast.
    """
    with TestClient(app) as c:
        yield c


# --- /health -----------------------------------------------------------------


def test_health_ok(client: TestClient) -> None:
    """``GET /health`` returns 200, ``status == "ok"``, and a populated body."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


def test_health_index_built_at_is_valid_iso(client: TestClient) -> None:
    """``index_built_at`` is a parseable ISO 8601 timestamp."""
    response = client.get("/health")
    body = response.json()
    built_at = body["index_built_at"]
    # Should parse without error and carry timezone info (UTC).
    parsed = datetime.fromisoformat(built_at)
    assert parsed.tzinfo is not None, "index_built_at should be timezone-aware"


def test_health_entry_count_positive(client: TestClient) -> None:
    """``entry_count`` reflects the real loaded dataset (> 0 entries)."""
    response = client.get("/health")
    body = response.json()
    assert body["entry_count"] > 0


# --- /entries ----------------------------------------------------------------


def test_entries_default_pagination(client: TestClient) -> None:
    """``GET /entries`` with no params returns the first default page."""
    response = client.get("/entries")
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert body["count"] == len(body["entries"])
    assert body["count"] <= body["limit"]
    # total equals the health-reported entry_count (whole dataset).
    total = client.get("/health").json()["entry_count"]
    assert body["total"] == total


def test_entries_respects_limit(client: TestClient) -> None:
    """``limit`` caps the page size and is echoed."""
    response = client.get("/entries", params={"limit": 5})
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 5
    assert body["count"] == len(body["entries"]) == 5


def test_entries_pagination_offset_changes_slice(client: TestClient) -> None:
    """``offset`` shifts the returned slice (page 0 vs page 1 differ)."""
    first = client.get("/entries", params={"limit": 5, "offset": 0}).json()
    second = client.get("/entries", params={"limit": 5, "offset": 5}).json()
    assert first["count"] == 5
    assert second["count"] == 5
    first_ids = {e["id"] for e in first["entries"]}
    second_ids = {e["id"] for e in second["entries"]}
    assert first_ids.isdisjoint(second_ids), "paged slices must not overlap"


def test_entries_offset_beyond_end_returns_empty_200(client: TestClient) -> None:
    """An offset past the last entry yields an empty page with HTTP 200."""
    total = client.get("/health").json()["entry_count"]
    response = client.get("/entries", params={"limit": 10, "offset": total + 100})
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 0
    assert body["entries"] == []
    assert body["total"] == total


def test_entries_limit_out_of_range_rejected(client: TestClient) -> None:
    """``limit > 200`` is rejected with 422."""
    response = client.get("/entries", params={"limit": 201})
    assert response.status_code == 422


def test_entries_limit_zero_rejected(client: TestClient) -> None:
    """``limit < 1`` is rejected with 422."""
    response = client.get("/entries", params={"limit": 0})
    assert response.status_code == 422


def test_entries_negative_offset_rejected(client: TestClient) -> None:
    """``offset < 0`` is rejected with 422."""
    response = client.get("/entries", params={"offset": -1})
    assert response.status_code == 422


def test_entries_entry_shape(client: TestClient) -> None:
    """Each returned entry has the expected fields with correct types."""
    body = client.get("/entries", params={"limit": 1}).json()
    entry = body["entries"][0]
    assert isinstance(entry["id"], str)
    assert isinstance(entry["doctor_name"], str)
    assert isinstance(entry["location"], str)
    # Optional fields may be null but must be present as keys.
    for key in ("specialty", "facility", "phone", "notes"):
        assert key in entry


# --- /entries/{id} -----------------------------------------------------------


def test_get_entry_known_id(client: TestClient) -> None:
    """``GET /entries/ent-0001`` returns 200 with the right fields."""
    response = client.get("/entries/ent-0001")
    assert response.status_code == 200
    entry = response.json()
    assert entry["id"] == "ent-0001"
    assert entry["doctor_name"] == "Dobert Pop"
    assert entry["location"] == "Bukalest"
    assert entry["specialty"] == "Cardiology"


def test_get_entry_unknown_id_returns_404(client: TestClient) -> None:
    """An unknown id yields 404 with a ``detail`` body."""
    response = client.get("/entries/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert "detail" in body
    assert isinstance(body["detail"], str)
    assert "does-not-exist" in body["detail"]


def test_get_entry_matches_first_list_entry(client: TestClient) -> None:
    """The first entry from ``/entries`` is retrievable by id from ``/entries/{id}``."""
    listed = client.get("/entries", params={"limit": 1}).json()["entries"][0]
    fetched = client.get(f"/entries/{listed['id']}").json()
    assert fetched == listed


# --- /search -----------------------------------------------------------------


def test_search_bukalest_returns_bucharest(client: TestClient) -> None:
    """``GET /search?q=Bukalest`` surfaces entries whose location is Bucharest.

    This is the critical typo-bridging success criterion from PRODUCT.md: the
    query is a typo of ``Bucharest``, and fuzzy search must surface at least one
    entry whose *correct* location is ``Bucharest`` (not just the literal
    ``Bukalest`` typo entries).
    """
    response = client.get("/search", params={"q": "Bukalest"})
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "Bukalest"
    assert body["count"] == len(body["hits"])
    assert body["count"] > 0, "expected at least one hit for 'Bukalest'"
    bucharest_hits = [h for h in body["hits"] if h["entry"]["location"] == "Bucharest"]
    assert bucharest_hits, (
        "fuzzy search should surface Bucharest entries for the 'Bukalest' typo"
    )
    # The best Bucharest hit should be a location-field match above threshold.
    best = max(bucharest_hits, key=lambda h: h["score"])
    assert best["field"] == "location"
    assert best["score"] >= 70.0


def test_search_dobert_returns_robert(client: TestClient) -> None:
    """``GET /search?q=Dobert`` surfaces ``Robert...`` doctors.

    The second critical typo-bridging criterion: the query is a typo of
    ``Robert`` and at least one returned entry must have a doctor name starting
    with ``Robert`` (the correct form), not just literal ``Dobert`` entries.
    """
    response = client.get("/search", params={"q": "Dobert"})
    assert response.status_code == 200
    body = response.json()
    assert body["count"] > 0, "expected at least one hit for 'Dobert'"
    robert_hits = [
        h
        for h in body["hits"]
        if h["entry"]["doctor_name"].lower().startswith("robert")
    ]
    assert robert_hits, (
        "fuzzy search should surface correct 'Robert' entries for the 'Dobert' typo"
    )
    best = max(robert_hits, key=lambda h: h["score"])
    assert best["field"] == "doctor_name"
    assert best["score"] >= 70.0


def test_search_hit_shape(client: TestClient) -> None:
    """Each hit has ``entry`` (with the entry fields), ``score``, and ``field``."""
    body = client.get("/search", params={"q": "Bucharest"}).json()
    assert body["hits"], "expected hits for a known term"
    hit = body["hits"][0]
    assert set(hit.keys()) >= {"entry", "score", "field"}
    assert isinstance(hit["score"], float | int)
    for key in ("id", "doctor_name", "location"):
        assert key in hit["entry"]


def test_search_missing_q_returns_422(client: TestClient) -> None:
    """Omitting ``q`` is rejected with 422."""
    response = client.get("/search")
    assert response.status_code == 422


def test_search_empty_q_returns_422(client: TestClient) -> None:
    """An empty ``q`` is rejected (``min_length=1``)."""
    response = client.get("/search", params={"q": ""})
    assert response.status_code == 422


def test_search_whitespace_q_returns_422(client: TestClient) -> None:
    """A whitespace-only ``q`` is trimmed and rejected as empty (422)."""
    response = client.get("/search", params={"q": "   "})
    assert response.status_code == 422


def test_search_unknown_field_returns_422(client: TestClient) -> None:
    """An unknown ``field`` value is rejected with 422."""
    response = client.get("/search", params={"q": "Robert", "field": "phone"})
    assert response.status_code == 422
    assert "phone" in response.json()["detail"]


def test_search_limit_capped_and_echoed(client: TestClient) -> None:
    """``limit`` is echoed and bounds the number of returned hits."""
    response = client.get("/search", params={"q": "Robert", "limit": 3})
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 3
    assert body["count"] <= 3


def test_search_limit_out_of_range_rejected(client: TestClient) -> None:
    """``limit > 100`` is rejected with 422."""
    response = client.get("/search", params={"q": "Robert", "limit": 101})
    assert response.status_code == 422


def test_search_results_ranked_by_score_descending(client: TestClient) -> None:
    """Hit scores are non-increasing from first to last."""
    body = client.get("/search", params={"q": "Bucharest", "limit": 10}).json()
    scores = [h["score"] for h in body["hits"]]
    assert scores == sorted(scores, reverse=True)


def test_search_field_location_restricts_matching(client: TestClient) -> None:
    """``field=location`` restricts matches to the location field.

    Searching a doctor-name term with ``field=location`` must return fewer (or
    no) hits than a cross-field search, proving the restriction is applied.
    Also, every returned hit's ``field`` is ``"location"``.
    """
    term = "Robert"
    scoped = client.get("/search", params={"q": term, "field": "location"}).json()
    cross = client.get("/search", params={"q": term}).json()
    assert scoped["count"] <= cross["count"]
    # A doctor-name term is a weak location match; scoped should be much smaller.
    assert scoped["count"] < cross["count"]
    for hit in scoped["hits"]:
        assert hit["field"] == "location"


def test_search_field_doctor_name_matches_only_names(client: TestClient) -> None:
    """``field=doctor_name`` matches doctor names and reports that field."""
    body = client.get("/search", params={"q": "Robert", "field": "doctor_name"}).json()
    assert body["count"] > 0
    for hit in body["hits"]:
        assert hit["field"] == "doctor_name"


@pytest.mark.parametrize("field", list(INDEXED_FIELDS))
def test_search_each_indexed_field_accepted(client: TestClient, field: str) -> None:
    """Every value in ``INDEXED_FIELDS`` is accepted by the ``field`` param."""
    response = client.get("/search", params={"q": "Robert", "field": field})
    assert response.status_code == 200
    assert response.json()["field"] == field
