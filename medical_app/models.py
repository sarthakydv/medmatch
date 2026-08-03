"""Pydantic entry model — authoritative schema for a medical entry.

The authoritative field-by-field definition lives in
``docs/DATA_SCHEMA.md``; this module provides the validating Pydantic v2
model the loader (``feat-003``) and search index (``feat-004``) consume.

Design notes:
- ``extra="allow"`` keeps unknown fields forward-compatible (the client may
  add new keys without invalidating the dump).
- ``str_strip_whitespace=True`` normalizes incoming strings before the
  non-empty checks run, so leading/trailing whitespace can't smuggle an
  otherwise-empty value past validation.
- Only ``id``, ``doctor_name`` and ``location`` are required. Optional
  fields default to ``None``. Note: cross-entry ``id`` uniqueness is enforced
  at load time (the loader sees the whole dump), not here.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


class MedicalEntry(BaseModel):
    """One medical entry from the JSON dump.

    Fields match ``docs/DATA_SCHEMA.md`` exactly. Required: ``id``,
    ``doctor_name``, ``location``. The rest are best-effort (``None`` when
    absent). ``doctor_name`` and ``location`` frequently contain speech-to-text
    transcription typos and are the primary targets for fuzzy search.
    """

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    id: str
    doctor_name: str
    location: str
    specialty: str | None = None
    facility: str | None = None
    phone: str | None = None
    notes: str | None = None

    @field_validator("id", "doctor_name", "location", mode="after")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        """Reject empty required fields.

        Whitespace has already been stripped by ``str_strip_whitespace`` at
        this point, so a blank value means a genuinely empty/whitespace-only
        input — which is invalid per the schema.
        """
        if not value:
            msg = "must be a non-empty string"
            raise ValueError(msg)
        return value


__all__ = ["MedicalEntry"]
