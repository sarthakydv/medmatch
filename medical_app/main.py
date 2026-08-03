"""uvicorn entrypoint.

Run the API server. Concrete wiring (host/port/log level from config,
index preload) lands in ``feat-005`` / ``feat-007``. Today this exposes a
``main()`` that starts uvicorn against :data:`medical_app.api.app` using the
config defaults.
"""

from __future__ import annotations

import uvicorn

from medical_app.api import app
from medical_app.config import settings


def main() -> None:
    """Start the uvicorn server using the configured host/port/log level."""
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()

__all__ = ["main"]
