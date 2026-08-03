"""Pydantic entry model.

This is a minimal stub for ``feat-001``. The authoritative schema is defined
in ``feat-002`` (``docs/DATA_SCHEMA.md``) and the validating model in
``feat-003``. See the data schema doc for field-by-field requirements.
"""

from __future__ import annotations

from pydantic import BaseModel


class MedicalEntry(BaseModel):
    """Minimal placeholder model.

    The full model (``feat-003``) will add validation rules: ``id`` must be a
    non-empty unique string; ``doctor_name`` and ``location`` must be non-empty;
    optional fields (``specialty``, ``facility``, ``phone``, ``notes``) are
    best-effort. Model config (extra="allow") is forward-compatible with
    unknown fields.
    """

    id: str
    doctor_name: str
    location: str


__all__ = ["MedicalEntry"]
