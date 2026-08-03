"""Structured-ish logging configuration (feat-007).

Configures the root logger with a single, consistent single-line handler so
that application logs (load, reload, request errors) are emitted in a parseable
format with timestamp, level, logger name and message. This is intentionally
dependency-free: a plain ``logging.Formatter`` (not JSON) is preferred for a
low-maintenance service — it is easy to read in a terminal and easy to grep.

The :func:`setup_logging` entrypoint is **idempotent**: calling it more than
once (e.g. once from ``main()`` and again from the FastAPI lifespan) does not
stack duplicate handlers. It is safe to call from both entrypoints.

Scope:
- Only the root + app loggers are configured here. Uvicorn's access/error
  loggers are left to their own config (we do not silence or re-handler them).
- Setting the root logger level is fine and expected.
"""

from __future__ import annotations

import logging

from medical_app.config import settings

#: Single-line log format: timestamp, level, logger name, message.
LOG_FORMAT: str = "%(asctime)s %(levelname)s %(name)s %(message)s"

#: Date format (ISO-ish, sortable).
_DATE_FORMAT: str = "%Y-%m-%dT%H:%M:%S%z"

#: Marker stored on handlers we own, so re-calling :func:`setup_logging` can
#: recognize (and replace) only our handler rather than clobbering handlers
#: attached by other libraries (or by uvicorn).
_OWNED_HANDLER_ATTR: str = "_medical_app_owned"


def setup_logging(level: str | None = None) -> None:
    """Configure root logging with a single owned handler.

    Idempotent: if called again, the previously-installed handler (if any) is
    replaced rather than duplicated, and handlers we did not install are left
    alone. The root logger level is set from ``level`` (if given) or
    :attr:`medical_app.config.Settings.log_level`.

    Args:
        level: Optional level name (e.g. ``"DEBUG"``). When ``None`` the level
            is read from :data:`medical_app.config.settings.log_level`.
    """
    root = logging.getLogger()

    effective_level = level if level is not None else settings.log_level
    root.setLevel(effective_level.upper())

    # Remove any handler we previously installed (idempotent: avoid stacking).
    for handler in list(root.handlers):
        if getattr(handler, _OWNED_HANDLER_ATTR, False):
            root.removeHandler(handler)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=_DATE_FORMAT))
    handler.setLevel(effective_level.upper())
    setattr(handler, _OWNED_HANDLER_ATTR, True)
    root.addHandler(handler)


__all__ = ["LOG_FORMAT", "setup_logging"]
