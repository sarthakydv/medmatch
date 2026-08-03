"""JSON dump loader / data pipeline.

This is a minimal stub for ``feat-001``. The full implementation lands in
``feat-003``: read the JSON array from a configurable path, validate each
entry against :class:`medical_app.models.MedicalEntry`, normalize fields
(strip, lowercase for indexing), and return a typed collection. Malformed
entries are skipped, counted, and logged rather than aborting the whole load
(resilience over strictness).
"""

from __future__ import annotations

from collections.abc import Sequence

from medical_app.models import MedicalEntry


def load_entries(path: str) -> Sequence[MedicalEntry]:
    """Load and validate entries from the JSON dump at ``path``.

    Returns an empty sequence today (no data yet). ``feat-003`` will implement
    the real read/validate/normalize pipeline and raise or skip on malformed
    input per the data schema doc.
    """
    _ = path  # data path will be used in feat-003
    return []


__all__ = ["load_entries"]
