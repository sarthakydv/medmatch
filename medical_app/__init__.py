"""Medical Entries Data Pipeline & Search API.

A single FastAPI service that loads a JSON dump of medical entries, builds an
in-memory typo-tolerant search index (rapidfuzz), and serves read/search
endpoints. See ``docs/ARCHITECTURE.md`` for the design overview.

This package is bootstrapped in ``feat-001``; concrete modules are filled in by
later features (feat-002 through feat-008).
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
