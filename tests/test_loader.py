"""Unit tests for :mod:`medical_app.loader` (feat-003 data pipeline).

Covers the contract from ``docs/DATA_SCHEMA.md`` / ``docs/ARCHITECTURE.md``:

- **Happy path**: the real ``data/mock_entries.json`` loads cleanly.
- **Malformed entries are skipped, not fatal**: invalid records and
  duplicate ``id``s are dropped, counted, and the load keeps going.
- **Missing file** raises :class:`LoaderError`.
- **Empty file** (0 bytes / whitespace-only / ``[]``) yields an empty dataset.
- **Structural errors** (malformed JSON, top-level not a JSON array) raise
  :class:`LoaderError`.
- :func:`normalized_text` lowercases the searchable fields for the index.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from medical_app.loader import (
    DEFAULT_DATA_PATH,
    LoaderError,
    LoadResult,
    load_entries,
    normalized_text,
)
from medical_app.models import MedicalEntry

REPO_ROOT = Path(__file__).resolve().parent.parent
MOCK_ENTRIES_PATH = REPO_ROOT / "data" / "mock_entries.json"


def _write_json(path: Path, data: Any) -> Path:
    """Serialize ``data`` to ``path`` as JSON and return ``path``."""
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# --- Happy path --------------------------------------------------------------


def test_load_mock_entries_happy_path() -> None:
    """Loading the real dump returns fully validated entries with nothing dropped."""
    result = load_entries(MOCK_ENTRIES_PATH)

    assert isinstance(result, LoadResult)
    assert result.loaded == 73
    assert result.total == 73
    assert result.skipped == 0
    assert len(result.entries) == 73

    # Every returned entry is validated and in first-seen order.
    assert all(isinstance(e, MedicalEntry) for e in result.entries)

    ids = [e.id for e in result.entries]
    assert len(ids) == len(set(ids)), "ids must be unique"


def test_default_data_path_resolves_to_mock_entries() -> None:
    """The module default points at the shipped synthetic dump."""
    assert DEFAULT_DATA_PATH == Path("data/mock_entries.json")


def test_path_accepts_str_and_path(tmp_path: Path) -> None:
    """``load_entries`` accepts either a ``str`` or a ``Path`` for the path arg."""
    payload = [{"id": "a", "doctor_name": "Dr A", "location": "X"}]
    file_path = _write_json(tmp_path / "entries.json", payload)

    via_path = load_entries(file_path)
    via_str = load_entries(str(file_path))

    assert via_path == via_str
    assert via_str.loaded == 1


# --- Empty datasets ----------------------------------------------------------


def test_empty_file_returns_empty_result(tmp_path: Path) -> None:
    """A 0-byte file is a valid empty dataset (0 entries, 0 skipped)."""
    file_path = tmp_path / "empty.json"
    file_path.write_bytes(b"")

    result = load_entries(file_path)

    assert result.entries == []
    assert result.loaded == 0
    assert result.total == 0
    assert result.skipped == 0


def test_whitespace_only_file_returns_empty_result(tmp_path: Path) -> None:
    """A whitespace-only file is treated the same as an empty file."""
    file_path = tmp_path / "whitespace.json"
    file_path.write_text("   \n\t  \n", encoding="utf-8")

    result = load_entries(file_path)

    assert result.entries == []
    assert result.loaded == 0
    assert result.skipped == 0


def test_empty_array_returns_empty_result(tmp_path: Path) -> None:
    """``[]`` is a valid empty array -> empty dataset."""
    file_path = _write_json(tmp_path / "empty_array.json", [])

    result = load_entries(file_path)

    assert result.entries == []
    assert result.loaded == 0
    assert result.total == 0
    assert result.skipped == 0


# --- Structural errors (hard failures) ---------------------------------------


@pytest.mark.parametrize(
    "contents",
    [
        b"",  # 0 bytes (sanity: handled before structural-error cases)
        # N.B. the empty-bytes case above is NOT an error; keep it distinct from
        # the structural failures below.
    ],
)
def test_empty_bytes_not_a_structural_error(tmp_path: Path, contents: bytes) -> None:
    """Guard: 0-byte input must NOT raise (re-asserts the empty-file contract)."""
    file_path = tmp_path / "zero.json"
    file_path.write_bytes(contents)
    result = load_entries(file_path)
    assert result == LoadResult(entries=[], skipped=0, total=0)


@pytest.mark.parametrize(
    "contents,desc",
    [
        ("{not valid json", "malformed JSON (truncated object)"),
        ("[1, 2, ", "malformed JSON (truncated array)"),
        ('{"id": "x"}', "top-level JSON object, not an array"),
        ('"hello"', "top-level JSON string, not an array"),
        ("42", "top-level JSON number, not an array"),
        ("null", "top-level JSON null, not an array"),
        ("true", "top-level JSON bool, not an array"),
    ],
)
def test_structural_errors_raise_loader_error(
    tmp_path: Path, contents: str, desc: str
) -> None:
    """Malformed JSON or a non-array top-level value raises ``LoaderError``."""
    file_path = tmp_path / "bad.json"
    file_path.write_text(contents, encoding="utf-8")

    with pytest.raises(LoaderError):
        load_entries(file_path)


def test_missing_file_raises_loader_error(tmp_path: Path) -> None:
    """A path that does not exist raises ``LoaderError`` (wraps FileNotFoundError)."""
    missing = tmp_path / "does_not_exist.json"

    with pytest.raises(LoaderError):
        load_entries(missing)


# --- Resilience: malformed entries are skipped, not fatal --------------------


def test_malformed_and_duplicate_entries_are_skipped(tmp_path: Path) -> None:
    """Invalid records and duplicate ids are dropped + counted; load keeps going."""
    payload: list[dict[str, Any]] = [
        # 0: valid
        {"id": "ent-1", "doctor_name": "Robert Pop", "location": "Bucharest"},
        # 1: invalid - missing required id
        {"doctor_name": "No Id", "location": "X"},
        # 2: invalid - empty doctor_name (after strip)
        {"id": "ent-2", "doctor_name": "   ", "location": "Y"},
        # 3: invalid - missing required location
        {"id": "ent-3", "doctor_name": "No Loc"},
        # 4: valid
        {"id": "ent-4", "doctor_name": "Jane Doe", "location": "Cluj"},
        # 5: duplicate id of entry 0 -> skipped
        {"id": "ent-1", "doctor_name": "Dup", "location": "Z"},
        # 6: valid (extra/unknown fields tolerated)
        {
            "id": "ent-5",
            "doctor_name": "Extra",
            "location": "Iasi",
            "region": "SE",
        },
    ]
    file_path = _write_json(tmp_path / "mixed.json", payload)

    result = load_entries(file_path)

    # 3 valid survivors: ent-1, ent-4, ent-5; 4 dropped.
    assert result.loaded == 3
    assert result.total == len(payload)
    assert result.skipped == 4
    assert result.total == result.loaded + result.skipped

    survivor_ids = [e.id for e in result.entries]
    assert survivor_ids == ["ent-1", "ent-4", "ent-5"]
    assert len(set(survivor_ids)) == len(survivor_ids)

    # Original field values are preserved (not lowercased) for API responses.
    first = result.entries[0]
    assert first.doctor_name == "Robert Pop"
    assert first.location == "Bucharest"


def test_non_object_record_is_skipped(tmp_path: Path) -> None:
    """Array elements that are not JSON objects are skipped + counted."""
    payload: list[Any] = [
        {"id": "ok", "doctor_name": "Dr", "location": "Here"},
        "not-an-object",
        12345,
        None,
        ["a", "list"],
    ]
    file_path = _write_json(tmp_path / "non_objects.json", payload)

    result = load_entries(file_path)

    assert result.loaded == 1
    assert result.total == 5
    assert result.skipped == 4
    assert result.entries[0].id == "ok"


def test_load_does_not_abort_after_first_invalid_entry(tmp_path: Path) -> None:
    """A bad entry at the head does not stop later valid entries from loading."""
    payload: list[dict[str, Any]] = [
        {"id": "", "doctor_name": "Bad", "location": "X"},  # invalid first
        {"id": "good", "doctor_name": "Good", "location": "Y"},  # valid second
    ]
    file_path = _write_json(tmp_path / "bad_first.json", payload)

    result = load_entries(file_path)

    assert result.loaded == 1
    assert result.skipped == 1
    assert result.entries[0].id == "good"


# --- normalized_text ---------------------------------------------------------


def test_normalized_text_lowercases_and_joins_fields() -> None:
    """Searchable fields are lowercased and joined; None specialty is omitted."""
    entry = MedicalEntry(
        id="x",
        doctor_name="Robert Pop",
        location="Bucharest",
        specialty="Cardiology",
    )
    assert normalized_text(entry) == "robert pop bucharest cardiology"


def test_normalized_text_omits_missing_specialty() -> None:
    """A None specialty does not contribute a stray space."""
    entry = MedicalEntry(id="x", doctor_name="Dr A", location="Town")
    assert normalized_text(entry) == "dr a town"


def test_normalized_text_handles_already_stripped_whitespace() -> None:
    """The model strips whitespace; normalization just lowercases."""
    entry = MedicalEntry(
        id="x",
        doctor_name="  Multi Word  ",
        location="  Some Place  ",
    )
    assert normalized_text(entry) == "multi word some place"
