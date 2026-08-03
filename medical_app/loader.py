"""JSON dump loader / data pipeline.

Reads the client's JSON dump (a top-level JSON array of entry objects) from a
configurable path, validates each entry against
:class:`medical_app.models.MedicalEntry`, enforces cross-entry ``id``
uniqueness, and returns a typed :class:`LoadResult`.

Resilience over strictness (per ``docs/DATA_SCHEMA.md``): individual entries
that fail validation, and later duplicates of an already-seen ``id``, are
**skipped, counted, and logged** — they never abort the whole load. Structural
problems with the file itself (missing file, unreadable, not valid JSON, or a
top-level value that is not a JSON array) are treated as hard errors and raise
:class:`LoaderError`, since there is no sensible partial result.

Error handling summary (see ``_skip_reason`` / ``load_entries``):
- Missing file            -> ``LoaderError`` (wraps ``FileNotFoundError``).
- Empty file (0 bytes)    -> valid empty dataset: ``LoadResult([], 0, 0)``.
- ``[]`` (empty array)    -> valid empty dataset: ``LoadResult([], 0, 0)``.
- Non-JSON / malformed    -> ``LoaderError`` (wraps ``json.JSONDecodeError``).
- Top-level not a list    -> ``LoaderError`` (the dump must be a JSON array).
- Malformed entry         -> skip + count (resilience rule), do NOT abort.
- Duplicate ``id``        -> skip the later duplicate + count (resilience rule).

The returned :class:`MedicalEntry` objects keep their original (stripped)
field values for API responses. :func:`normalized_text` exposes a lowercased
concatenation of the searchable fields so the fuzzy index (feat-004) can build
on a stable, normalized string without re-deriving it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from medical_app.models import MedicalEntry

logger = logging.getLogger(__name__)

DEFAULT_DATA_PATH = Path("data/mock_entries.json")


class LoaderError(Exception):
    """Raised when the dump file cannot be read into a usable dataset.

    Covers structural problems (missing file, unreadable bytes, malformed
    JSON, top-level value is not a JSON array). Individual entry validation
    failures are NOT loader errors — those are skipped and counted.
    """


@dataclass(frozen=True, slots=True)
class LoadResult:
    """Outcome of a :func:`load_entries` run.

    Attributes:
        entries: Validated entries (original/stripped field values preserved),
            in first-seen order, with unique ``id`` values. Suitable for API
            responses.
        skipped: Count of entries dropped during load (failed validation or
            duplicate ``id``). ``0`` means a fully clean load.
        total: Total number of raw records read from the dump array, i.e.
            ``len(entries) + skipped``.
    """

    entries: list[MedicalEntry]
    skipped: int = 0
    total: int = 0

    @property
    def loaded(self) -> int:
        """Number of entries that survived validation (alias for clarity)."""
        return len(self.entries)


def normalized_text(entry: MedicalEntry) -> str:
    """Return a lowercased, space-joined blob of the searchable fields.

    The fuzzy index (feat-004) consumes this. Searchable fields are
    ``doctor_name``, ``location`` (both typo-prone per the schema) and
    ``specialty`` (used for filtering). Optional fields that are ``None`` are
    omitted so they don't contribute stray spaces. Whitespace has already been
    stripped by the Pydantic model; here we only lowercase and join.

    Args:
        entry: A validated :class:`MedicalEntry`.

    Returns:
        A single lowercased string of the form
        ``"<doctor_name> <location> [<specialty>]"``.
    """
    parts: list[str] = [entry.doctor_name, entry.location]
    if entry.specialty is not None:
        parts.append(entry.specialty)
    return " ".join(parts).lower()


def load_entries(
    path: str | Path = DEFAULT_DATA_PATH,
) -> LoadResult:
    """Load and validate the JSON dump at ``path``.

    Reads the file, parses it as a JSON array, validates each record against
    :class:`MedicalEntry`, drops invalid or duplicate-``id`` records (counting
    and logging them), and returns a :class:`LoadResult`.

    Args:
        path: Path to the JSON dump. Defaults to :data:`DEFAULT_DATA_PATH`.

    Returns:
        A :class:`LoadResult` whose ``entries`` are the validated records,
        ``skipped`` is the count of dropped records, and ``total`` is the raw
        record count.

    Raises:
        LoaderError: If the file is missing/unreadable, is not valid JSON, or
            its top-level value is not a JSON array. (Malformed individual
            entries are NOT raised — they are skipped + counted.)
    """
    file_path = Path(path)
    raw_records = _read_and_parse(file_path)

    entries: list[MedicalEntry] = []
    seen_ids: set[str] = set()
    skipped = 0
    total = len(raw_records)

    for index, record in enumerate(raw_records):
        entry, reason = _validate_record(record, index, seen_ids)
        if entry is None:
            skipped += 1
            logger.warning("Skipping entry %d in %s: %s", index, file_path, reason)
            continue
        entries.append(entry)
        seen_ids.add(entry.id)

    return LoadResult(entries=entries, skipped=skipped, total=total)


def _read_and_parse(file_path: Path) -> list[object]:
    """Read ``file_path`` and return its top-level JSON array.

    Args:
        file_path: Path to the dump file.

    Returns:
        The parsed list of raw records. An empty file or a file containing
        only whitespace yields an empty list (treated as a valid empty dump).

    Raises:
        LoaderError: If the file is missing/unreadable, is not valid JSON, or
            its top-level value is not a list.
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        msg = f"Data file not found: {file_path}"
        raise LoaderError(msg) from exc
    except OSError as exc:
        msg = f"Unable to read data file {file_path}: {exc}"
        raise LoaderError(msg) from exc

    # An empty (or whitespace-only) file is a valid empty dataset.
    if not text.strip():
        return []

    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError as exc:
        msg = f"Data file {file_path} is not valid JSON: {exc}"
        raise LoaderError(msg) from exc

    if not isinstance(parsed, list):
        msg = (
            f"Data file {file_path} must contain a JSON array at the top "
            f"level, got {type(parsed).__name__}"
        )
        raise LoaderError(msg)

    return parsed


def _validate_record(
    record: object,
    index: int,
    seen_ids: set[str],
) -> tuple[MedicalEntry | None, str | None]:
    """Validate a single raw record.

    Args:
        record: One element from the top-level JSON array.
        index: Position of the record in the array (for diagnostics).
        seen_ids: Set of ``id`` values already accepted this load.

    Returns:
        A ``(entry, reason)`` pair. On success ``entry`` is the validated
        :class:`MedicalEntry` and ``reason`` is ``None``. On failure ``entry``
        is ``None`` and ``reason`` is a short human-readable string describing
        why the record was skipped.
    """
    if not isinstance(record, dict):
        return None, f"record {index} is not a JSON object ({type(record).__name__})"

    try:
        entry = MedicalEntry.model_validate(record)
    except ValidationError as exc:
        return None, f"record {index} failed validation: {exc}"

    if entry.id in seen_ids:
        return None, f"record {index} has duplicate id {entry.id!r}"

    return entry, None


def _main() -> int:
    """Module-loadable entrypoint: load the default dump and print a summary.

    Run via ``python -m medical_app.loader``. Returns a process exit code
    (``0`` on success, non-zero on a :class:`LoaderError`). Intended for
    manual sanity checks, not for the service startup path.
    """
    try:
        result = load_entries()
    except LoaderError as exc:
        print(f"Loader error: {exc}")
        return 1
    print(
        f"Loaded {result.loaded}/{result.total} entries from "
        f"{DEFAULT_DATA_PATH} (skipped {result.skipped})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "DEFAULT_DATA_PATH",
    "LoadResult",
    "LoaderError",
    "load_entries",
    "normalized_text",
]
