"""Application configuration (env-driven settings).

Configuration is loaded from environment variables (and optionally a ``.env``
file) via ``pydantic-settings``. All settings have sensible defaults, so the
service runs on a clean machine with no env vars and no ``.env`` file — the
defaults match the documented values in ``docs/ARCHITECTURE.md``.

Environment variables (prefix ``MEDICAL_``):

- ``MEDICAL_DATA_PATH`` (default ``data/mock_entries.json``): path to the
  JSON dump to load + index.
- ``MEDICAL_REFRESH_INTERVAL_SECONDS`` (default ``86400``, i.e. 24h): seconds
  between scheduled reloads (``0`` disables the scheduler).
- ``MEDICAL_HOST`` (default ``0.0.0.0``): API host to bind.
- ``MEDICAL_PORT`` (default ``8000``): API port to bind (1-65535).
- ``MEDICAL_LOG_LEVEL`` (default ``INFO``): root log level, one of
  DEBUG/INFO/WARNING/ERROR/CRITICAL (case-insensitive).
- ``MEDICAL_FUZZY_THRESHOLD`` (default ``70.0``): default minimum
  fuzzy-match similarity score (0-100) for the search index.

Field-to-env mapping: with ``env_prefix="MEDICAL_"``, a field named
``data_path`` reads ``MEDICAL_DATA_PATH``, etc.

A ``.env`` file at the working directory is also read if present
(``env_file=".env"``), but it is NOT required — environment variables and the
built-in defaults are always sufficient.
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: The standard Python logging level names accepted by :class:`Settings`.
_VALID_LOG_LEVELS: frozenset[str] = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
)


class Settings(BaseSettings):
    """Env-driven application settings.

    All fields default to the values documented in ``docs/ARCHITECTURE.md`` so
    the service starts cleanly with no environment configuration. The
    ``MEDICAL_`` env prefix maps each field to its ``MEDICAL_<FIELD>`` variable
    (e.g. ``data_path`` -> ``MEDICAL_DATA_PATH``).

    A module-level singleton :data:`settings` is constructed at import time so
    existing imports (``from medical_app.config import settings``) keep working.
    Constructing it reads env vars / ``.env`` but does no I/O beyond that (no
    file is opened for the data dump here).
    """

    model_config = SettingsConfigDict(
        env_prefix="MEDICAL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Path to the JSON dump to load + index.
    data_path: str = "data/mock_entries.json"
    # Seconds between scheduled reloads (0 disables the scheduler).
    refresh_interval_seconds: int = 86_400
    # API bind host.
    host: str = "0.0.0.0"
    # API bind port (1..65535).
    port: int = Field(default=8000, ge=1, le=65535)
    # Root log level (one of DEBUG/INFO/WARNING/ERROR/CRITICAL).
    log_level: str = "INFO"
    # Default minimum fuzzy-match similarity score (0..100) for the search index.
    fuzzy_threshold: float = Field(default=70.0, ge=0.0, le=100.0)

    @field_validator("refresh_interval_seconds")
    @classmethod
    def _interval_non_negative(cls, value: int) -> int:
        """Reject negative intervals (0 is valid: it disables the scheduler)."""
        if value < 0:
            msg = "refresh_interval_seconds must be >= 0 (0 disables the scheduler)"
            raise ValueError(msg)
        return value

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        """Uppercase + validate ``log_level`` against the standard level names.

        Accepts any case (``info``, ``Info``, ``INFO``) and normalizes to the
        canonical uppercase form so downstream consumers (``logging`` /
        ``uvicorn``) receive a consistent value.
        """
        normalized = value.strip().upper()
        if normalized not in _VALID_LOG_LEVELS:
            allowed = ", ".join(sorted(_VALID_LOG_LEVELS))
            msg = (
                f"log_level must be one of {allowed} (case-insensitive), got {value!r}"
            )
            raise ValueError(msg)
        return normalized


settings = Settings()

__all__ = ["Settings", "settings"]
