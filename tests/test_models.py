"""Unit tests for :class:`medical_app.models.MedicalEntry`.

Covers the contract from ``docs/DATA_SCHEMA.md``: required fields must be
non-empty (after stripping), optional fields default to ``None``, and unknown
extra fields are tolerated (forward-compatible). Cross-entry ``id`` uniqueness
is the loader's responsibility (feat-003), not the model's, so it is not
asserted here.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from medical_app.models import MedicalEntry


def test_valid_entry_parses() -> None:
    """A complete, well-formed entry parses and keeps every field."""
    entry = MedicalEntry(
        id="ent-0001",
        doctor_name="Dobert Pop",
        specialty="Cardiology",
        location="Bukalest",
        facility="Central Clinic",
        phone="+40 700 000 000",
        notes="Follow-up in two weeks.",
    )
    assert entry.id == "ent-0001"
    assert entry.doctor_name == "Dobert Pop"
    assert entry.location == "Bukalest"
    assert entry.specialty == "Cardiology"
    assert entry.facility == "Central Clinic"
    assert entry.phone == "+40 700 000 000"
    assert entry.notes == "Follow-up in two weeks."


def test_required_fields_default_none() -> None:
    """Optional fields are absent-tolerant and default to ``None``."""
    entry = MedicalEntry(id="ent-0002", doctor_name="Jane Doe", location="Bucharest")
    assert entry.specialty is None
    assert entry.facility is None
    assert entry.phone is None
    assert entry.notes is None


def test_whitespace_is_stripped() -> None:
    """``str_strip_whitespace`` trims incoming strings."""
    entry = MedicalEntry(
        id="  ent-0003  ",
        doctor_name="  Robert Pop  ",
        location="  Bucharest  ",
    )
    assert entry.id == "ent-0003"
    assert entry.doctor_name == "Robert Pop"
    assert entry.location == "Bucharest"


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"id": "", "doctor_name": "X", "location": "Y"}, "id"),
        ({"id": "i", "doctor_name": "", "location": "Y"}, "doctor_name"),
        ({"id": "i", "doctor_name": "X", "location": ""}, "location"),
        # whitespace-only is empty after stripping
        ({"id": "   ", "doctor_name": "X", "location": "Y"}, "id"),
        ({"id": "i", "doctor_name": "   ", "location": "Y"}, "doctor_name"),
        ({"id": "i", "doctor_name": "X", "location": "   "}, "location"),
    ],
)
def test_empty_required_fields_raise(payload: dict, field: str) -> None:
    """Empty or whitespace-only required fields raise ``ValidationError``."""
    with pytest.raises(ValidationError) as exc_info:
        MedicalEntry(**payload)
    assert field in str(exc_info.value)


def test_extra_fields_are_allowed() -> None:
    """Unknown keys are kept (forward-compatible), not rejected."""
    entry = MedicalEntry(
        id="ent-0004",
        doctor_name="Robert Pop",
        location="Bucharest",
        region="SE Europe",  # unknown extra field
        external_id=42,
    )
    assert entry.model_extra == {"region": "SE Europe", "external_id": 42}


def test_missing_required_field_raises() -> None:
    """Omitting a required field raises ``ValidationError``."""
    with pytest.raises(ValidationError):
        MedicalEntry(id="ent-0005", location="Bucharest")  # type: ignore[call-arg]
