"""Application configuration (env-driven settings).

This is a minimal stub for ``feat-001``. The full implementation lands in
``feat-007`` and reads environment variables such as ``MEDICAL_DATA_PATH``,
``MEDICAL_REFRESH_INTERVAL_SECONDS``, ``MEDICAL_HOST``, ``MEDICAL_PORT`` and
``MEDICAL_LOG_LEVEL`` (see ``docs/ARCHITECTURE.md``).
"""

from __future__ import annotations


class Settings:
    """Placeholder settings container.

    ``feat-007`` will replace this with a ``pydantic-settings``-based class
    bound to environment variables. Kept as a plain class here so importing the
    package has no heavy side effects.
    """

    # Path to the JSON dump (default set in feat-007).
    data_path: str = "data/mock_entries.json"
    # Refresh interval in seconds (24h default).
    refresh_interval_seconds: int = 86_400
    # API host/port.
    host: str = "0.0.0.0"
    port: int = 8000
    # Log level.
    log_level: str = "INFO"


settings = Settings()

__all__ = ["Settings", "settings"]
