"""Smoke tests that confirm the package imports cleanly after bootstrap.

These exist so ``pytest`` has a passing test during ``feat-001``. Real unit
tests are added by later features.
"""

from __future__ import annotations

import medical_app


def test_package_imports() -> None:
    """The package exposes a version string after import."""
    assert medical_app.__version__ == "0.1.0"
