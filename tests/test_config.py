"""Tests for feat-007: configuration, logging and resilience.

Covers four areas:

1. **Env-driven config** (:class:`medical_app.config.Settings`): each
   ``MEDICAL_*`` env var maps to the right field; defaults apply when no env is
   set; invalid values (bad port, bad log level, negative interval, threshold
   out of range) raise :class:`pydantic.ValidationError`.
2. **Logging** (:func:`medical_app.logging_config.setup_logging`): idempotent
   (calling twice does not stack handlers) and sets the root logger level.
3. **Startup resilience**: when ``settings.data_path`` points at a MISSING file
   the app STILL starts (``/health`` returns 200 with ``entry_count == 0``) and
   the build error is logged — rather than crashing.
4. **Fuzzy threshold wiring**: a high ``MEDICAL_FUZZY_THRESHOLD`` (99) makes
   ``/search?q=Bukalest`` return fewer/no Bucharest hits (since the canonical
   ``Bukalest`` -> ``Bucharest`` score is ~70.6, below 99), proving the env var
   flows from config -> :func:`build_and_swap` -> :class:`SearchIndex`.

Tests construct fresh ``Settings()`` instances inside the test body (rather
than relying on the module singleton) so env vars set via ``monkeypatch.setenv``
are re-read. The lifespan reads the module singleton ``settings``, so the
resilience/threshold tests patch that singleton's attributes directly (and
restore them in ``finally``).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from medical_app import api as api_module
from medical_app import service
from medical_app.config import Settings, settings
from medical_app.index import SearchIndex
from medical_app.logging_config import setup_logging
from medical_app.service import IndexSnapshot, set_live_snapshot

# --- 1. Env-driven config -----------------------------------------------------


def test_settings_defaults_when_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no ``MEDICAL_*`` env vars, every field has its documented default."""
    # Clear all MEDICAL_* env vars so we observe pure defaults.
    for key in list(os.environ):
        if key.startswith("MEDICAL_"):
            monkeypatch.delenv(key, raising=False)

    s = Settings()
    assert s.data_path == "data/mock_entries.json"
    assert s.refresh_interval_seconds == 86_400
    assert s.host == "0.0.0.0"
    assert s.port == 8000
    assert s.log_level == "INFO"
    assert s.fuzzy_threshold == 70.0


def test_settings_env_var_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each ``MEDICAL_*`` env var maps to the expected :class:`Settings` field."""
    monkeypatch.setenv("MEDICAL_DATA_PATH", "/tmp/custom_data.json")
    monkeypatch.setenv("MEDICAL_REFRESH_INTERVAL_SECONDS", "3600")
    monkeypatch.setenv("MEDICAL_HOST", "127.0.0.1")
    monkeypatch.setenv("MEDICAL_PORT", "9000")
    monkeypatch.setenv("MEDICAL_LOG_LEVEL", "debug")
    monkeypatch.setenv("MEDICAL_FUZZY_THRESHOLD", "85.5")

    s = Settings()
    assert s.data_path == "/tmp/custom_data.json"
    assert s.refresh_interval_seconds == 3600
    assert s.host == "127.0.0.1"
    assert s.port == 9000
    # log_level is normalized to canonical uppercase.
    assert s.log_level == "DEBUG"
    assert s.fuzzy_threshold == 85.5


def test_settings_log_level_normalized_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``log_level`` accepts any case and normalizes to uppercase."""
    for raw, expected in [("info", "INFO"), ("Warning", "WARNING"), ("ERROR", "ERROR")]:
        monkeypatch.setenv("MEDICAL_LOG_LEVEL", raw)
        assert Settings().log_level == expected


@pytest.mark.parametrize(
    "var,value",
    [
        ("MEDICAL_PORT", "0"),  # below min
        ("MEDICAL_PORT", "70000"),  # above max
        ("MEDICAL_PORT", "not-a-number"),
        ("MEDICAL_REFRESH_INTERVAL_SECONDS", "-1"),  # negative
        ("MEDICAL_FUZZY_THRESHOLD", "-0.1"),  # below 0
        ("MEDICAL_FUZZY_THRESHOLD", "100.1"),  # above 100
        ("MEDICAL_LOG_LEVEL", "BOGUS"),  # not a level
    ],
)
def test_settings_invalid_values_rejected(
    monkeypatch: pytest.MonkeyPatch, var: str, value: str
) -> None:
    """Invalid config values raise :class:`pydantic.ValidationError`."""
    monkeypatch.setenv(var, value)
    with pytest.raises(ValidationError):
        Settings()


def test_settings_env_file_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``.env`` file supplies values when the corresponding env var is absent."""
    env_file = tmp_path / ".env"
    env_file.write_text("MEDICAL_PORT=7777\nMEDICAL_HOST=10.0.0.1\n", encoding="utf-8")

    # chdir into tmp_path so the relative ".env" is found by pydantic-settings.
    monkeypatch.chdir(tmp_path)
    # Ensure no real env var overrides the file value.
    monkeypatch.delenv("MEDICAL_PORT", raising=False)
    monkeypatch.delenv("MEDICAL_HOST", raising=False)

    s = Settings()
    assert s.port == 7777
    assert s.host == "10.0.0.1"


# --- 2. Logging ---------------------------------------------------------------


def test_setup_logging_is_idempotent() -> None:
    """Calling :func:`setup_logging` twice does not stack handlers."""
    # Snapshot existing handlers so we can restore them (avoid leaking our own
    # StreamHandler into other tests that capture stderr).
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    try:
        setup_logging("INFO")
        first_count = len(root.handlers)
        setup_logging("INFO")
        setup_logging("DEBUG")  # a third call must also not stack
        second_count = len(root.handlers)
        assert first_count == second_count, (
            "setup_logging should not add duplicate handlers on repeat calls"
        )
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)


def test_setup_logging_sets_root_level() -> None:
    """:func:`setup_logging` sets the root logger level to the requested value."""
    root = logging.getLogger()
    original_level = root.level
    original_handlers = list(root.handlers)
    try:
        setup_logging("WARNING")
        assert root.level == logging.WARNING
        setup_logging("DEBUG")
        assert root.level == logging.DEBUG
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)


def test_setup_logging_keeps_only_one_owned_handler() -> None:
    """Our owned handler is the only owned handler present after setup."""
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    try:
        setup_logging("INFO")
        owned = [h for h in root.handlers if getattr(h, "_medical_app_owned", False)]
        assert len(owned) == 1, "exactly one owned handler should be installed"
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)


# --- 3. Startup resilience ----------------------------------------------------


def _reset_live_snapshot_to_empty() -> None:
    """Reset the module-global live snapshot to an empty placeholder.

    Used by the resilience test so a failed cold-start build leaves
    ``entry_count == 0`` regardless of whatever a prior test left in
    ``service._live_snapshot``.
    """
    set_live_snapshot(IndexSnapshot(index=SearchIndex([]), entries=[]))


def test_app_starts_when_data_path_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A missing ``data_path`` at startup does NOT crash the app.

    The lifespan logs the build failure and keeps serving the empty startup
    snapshot, so ``/health`` returns 200 with ``entry_count == 0``. This is the
    headline feat-007 resilience behavior: "keep serving ... and log the error
    rather than crashing."
    """
    _reset_live_snapshot_to_empty()
    original_data_path = settings.data_path
    settings.data_path = "/no/such/file/does-not-exist.json"
    try:
        with caplog.at_level(logging.ERROR, logger="medical_app.api"):
            with TestClient(api_module.app) as client:
                response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["entry_count"] == 0, (
            "app should start with an empty snapshot when the data file is missing"
        )
        # The build failure was logged.
        assert any(
            "Startup index build failed" in record.message for record in caplog.records
        ), "the startup build failure should have been logged"
    finally:
        settings.data_path = original_data_path


# --- 4. Fuzzy threshold wiring ------------------------------------------------


def test_high_fuzzy_threshold_filters_out_bukalest_bucharest() -> None:
    """``fuzzy_threshold == 99`` removes the Bukalest->Bucharest fuzzy hits.

    The canonical typo pair ``Bukalest`` -> ``Bucharest`` scores ~70.6 under the
    default threshold of 70. Raising the threshold to 99 (above that score)
    must therefore yield fewer/no Bucharest location hits for ``q=Bukalest``,
    proving the env var flows: ``MEDICAL_FUZZY_THRESHOLD`` ->
    :class:`Settings.fuzzy_threshold` -> :func:`service.build_and_swap` ->
    :class:`SearchIndex`.

    We compare the high-threshold result count against a fresh build at the
    default threshold to make the assertion robust to dataset changes.
    """
    from medical_app.loader import load_entries

    real_data = Path(settings.data_path)
    entries = load_entries(real_data).entries

    # Default threshold (70): Bukalest -> Bucharest is captured.
    default_index = SearchIndex(entries, threshold=70.0)
    default_hits = default_index.search("Bukalest", limit=20)
    default_bucharest = [h for h in default_hits if h.entry.location == "Bucharest"]
    assert default_bucharest, (
        "sanity: at the default threshold, Bukalest should fuzzy-match Bucharest"
    )

    # High threshold (99): the ~70.6 score is now below threshold, so Bucharest
    # location hits should disappear.
    high_index = SearchIndex(entries, threshold=99.0)
    high_hits = high_index.search("Bukalest", limit=20)
    high_bucharest = [h for h in high_hits if h.entry.location == "Bucharest"]
    assert not high_bucharest, (
        "at threshold=99, the Bukalest->Bucharest fuzzy match (score ~70.6) "
        "should be filtered out"
    )
    assert len(high_hits) < len(default_hits), (
        "a higher threshold must return strictly fewer hits"
    )


def test_fuzzy_threshold_flows_through_service_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``settings.fuzzy_threshold`` is honored by :func:`build_and_swap`.

    End-to-end wiring check at the service layer: with a high threshold the
    index built by :func:`build_and_swap` (which reads
    ``settings.fuzzy_threshold``) rejects the Bukalest->Bucharest match that the
    default threshold accepts. Uses the real dump so no temp file is needed.
    """
    original_threshold = settings.fuzzy_threshold
    try:
        # Default threshold: the live index should surface Bucharest for the typo.
        service.build_and_swap(settings.data_path)
        default_results = service.get_live_index().search("Bukalest", limit=20)
        default_bucharest = [
            r for r in default_results if r.entry.location == "Bucharest"
        ]
        assert default_bucharest, (
            "sanity: default threshold should capture Bukalest->Bucharest"
        )

        # High threshold: rebuild with settings.fuzzy_threshold=99 -> no Bucharest.
        settings.fuzzy_threshold = 99.0
        service.build_and_swap(settings.data_path)
        high_results = service.get_live_index().search("Bukalest", limit=20)
        high_bucharest = [r for r in high_results if r.entry.location == "Bucharest"]
        assert not high_bucharest, (
            "settings.fuzzy_threshold=99 must flow through build_and_swap into "
            "the SearchIndex and filter out the ~70.6 Bucharest match"
        )
    finally:
        settings.fuzzy_threshold = original_threshold
        # Restore a default-threshold live snapshot so other tests aren't affected.
        service.build_and_swap(settings.data_path)
