"""uvicorn entrypoint.

Run the API server. Wires the env-driven configuration (host/port/log level from
:mod:`medical_app.config`) and configures structured logging via
:func:`medical_app.logging_config.setup_logging` before starting uvicorn.
"""

from __future__ import annotations

import uvicorn

from medical_app.api import app
from medical_app.config import settings
from medical_app.logging_config import setup_logging


def main() -> None:
    """Start the uvicorn server using the configured host/port/log level."""
    # Configure logging before uvicorn starts so startup logs are formatted.
    # Idempotent; the FastAPI lifespan also calls this (harmless when run via
    # main() since the second call replaces, not duplicates, the handler).
    setup_logging()
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()

__all__ = ["main"]
