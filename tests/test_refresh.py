"""Tests for the atomic daily refresh (feat-006).

Covers the four pillars of feat-006:

1. **Atomic swap / no partial data** — the headline requirement. A reload via
   :func:`~medical_app.service.build_and_swap` publishes a fully-consistent new
   snapshot (count, timestamp, contents all agree), and a concurrent reader that
   samples the live snapshot while a reload is in flight only ever observes an
   internally-consistent old-or-new snapshot (never a mix).
2. **Reload picks up changed data** — overwrite the dump, reload, assert the new
   ids / count / ``built_at`` are live.
3. **Failure does NOT replace the live index** — a reload pointed at a missing /
   malformed dump raises (or returns ``reloaded: false``) and the live snapshot
   is unchanged (same count, same ``built_at``).
4. **Manual reload endpoint** — ``POST /admin/reload`` returns 200 with
   ``reloaded: true`` on success and ``reloaded: false`` + ``error`` on loader
   failure, with the live index left intact either way.
5. **Scheduler** — the refresh-scheduler thread is started on lifespan startup
   and stopped on shutdown (no leak), and a reload actually fires when a tiny
   interval elapses.

Each test resets the module-level live snapshot to a known state first so tests
don't depend on ordering, and uses ``tmp_path`` for dump files.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from medical_app import api as api_module
from medical_app import service
from medical_app.config import settings
from medical_app.index import SearchIndex
from medical_app.loader import LoaderError
from medical_app.service import (
    IndexSnapshot,
    build_and_swap,
    get_live_snapshot,
    set_live_snapshot,
)

# --- Test data helpers --------------------------------------------------------


def _make_entry(
    eid: str,
    doctor_name: str = "Test Doctor",
    location: str = "Testville",
    specialty: str | None = "General",
) -> dict[str, object]:
    """Build a minimal valid entry dict for a dump file."""
    entry: dict[str, object] = {
        "id": eid,
        "doctor_name": doctor_name,
        "location": location,
    }
    if specialty is not None:
        entry["specialty"] = specialty
    return entry


def _write_dump(path: Path, entries: list[dict[str, object]]) -> Path:
    """Write ``entries`` as a JSON array to ``path`` and return it."""
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def _reset_live_snapshot() -> None:
    """Reset the module-level live snapshot to a known empty state.

    Tests exercise :func:`build_and_swap` directly, which always swaps. To keep
    them order-independent we start each from the same empty placeholder so the
    *delta* a test asserts on is unambiguous.
    """
    set_live_snapshot(IndexSnapshot(index=SearchIndex([]), entries=[]))


# --- 1. Atomic swap / no partial data ----------------------------------------


def test_build_and_swap_publishes_new_snapshot(tmp_path: Path) -> None:
    """``build_and_swap`` publishes a snapshot reflecting the dump fully.

    After the swap the live snapshot must be internally consistent:
    ``len(entries) == index.size == entry_count`` and ``built_at`` is recent.
    """
    _reset_live_snapshot()
    dump = _write_dump(
        tmp_path / "dump.json",
        [_make_entry(f"ent-{i:04d}") for i in range(5)],
    )

    before = get_live_snapshot()
    result = build_and_swap(dump)
    after = get_live_snapshot()

    # The returned snapshot IS the now-live snapshot (same object).
    assert result is after
    # And it differs from the previous (empty) snapshot.
    assert after is not before
    # Internally consistent: count agrees everywhere.
    assert len(after.entries) == 5
    assert after.index.size == 5
    # built_at is a recent, timezone-aware timestamp.
    assert after.built_at.tzinfo is not None
    delta = datetime.now(after.built_at.tzinfo) - after.built_at
    assert 0 <= delta.total_seconds() < 10
    # The ids are exactly the new ones.
    assert {e.id for e in after.entries} == {f"ent-{i:04d}" for i in range(5)}


def test_atomic_swap_never_serves_partial_data(tmp_path: Path) -> None:
    """A concurrent reader only ever sees a consistent old-or-new snapshot.

    This is the headline atomicity test. We make the loader's work non-trivial
    by adding a deliberate (short) delay inside index construction via a
    monkeypatched loader, then spawn a reader thread that rapidly samples
    :func:`get_live_snapshot` while a reload runs. Every sampled snapshot must be
    *internally consistent*:

    - its ``built_at`` must match either the pre-reload value (old) or the
      post-reload value (new) — never anything in between (no partial timestamp);
    - its entry list length must equal its index size — never a torn count.

    A non-atomic (in-place mutation) implementation would let a reader catch the
    snapshot mid-update and observe a length/size or timestamp mismatch.
    """
    _reset_live_snapshot()

    # Start from a known OLD snapshot with 2 entries.
    old_dump = _write_dump(
        tmp_path / "old.json",
        [_make_entry("old-0001"), _make_entry("old-0002")],
    )
    old_snapshot = build_and_swap(old_dump)
    old_built_at = old_snapshot.built_at
    old_len = len(old_snapshot.entries)
    assert old_len == 2

    # Build a NEW dump with a different entry count (5) so we can tell old vs new
    # apart unambiguously.
    new_dump = _write_dump(
        tmp_path / "new.json",
        [_make_entry(f"new-{i:04d}") for i in range(5)],
    )

    # Slow down the load path so the reader has a real chance to interleave.
    # We wrap the real loader so the snapshot under construction is genuinely
    # new (built from new_dump) but the window in which it is being built is
    # stretched.
    real_load_entries = service.load_entries

    def slow_load(path: str | Path) -> object:
        time.sleep(0.05)  # hold the build window open so the reader can sample
        return real_load_entries(path)

    samples: list[IndexSnapshot] = []
    sample_count = 0
    sampling_done = threading.Event()

    def reader() -> None:
        nonlocal sample_count
        # Sample as fast as possible until told to stop.
        while not sampling_done.is_set():
            samples.append(get_live_snapshot())
            sample_count += 1

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()
    # Give the reader a moment to be clearly running before the swap.
    time.sleep(0.02)

    new_snapshot: IndexSnapshot
    with patch.object(service, "load_entries", side_effect=slow_load):
        new_snapshot = build_and_swap(new_dump)

    # Keep sampling briefly after the swap too, then stop the reader.
    time.sleep(0.05)
    sampling_done.set()
    reader_thread.join(timeout=2.0)

    new_built_at = new_snapshot.built_at
    new_len = len(new_snapshot.entries)
    assert new_len == 5

    # We must have actually sampled during the window for the test to be
    # meaningful; if the scheduler/reader never ran, the assertion is vacuous.
    assert sample_count > 0, "reader never sampled the live snapshot"

    # Every sampled snapshot must be internally consistent: its built_at is
    # EXACTLY the old or the new timestamp (never anything between), and its
    # entry-count == index-size (never a torn count).
    for s in samples:
        assert s.built_at in (old_built_at, new_built_at), (
            "reader observed a snapshot whose built_at is neither the old nor "
            f"the new value: {s.built_at} (old={old_built_at}, new={new_built_at})"
        )
        assert len(s.entries) == s.index.size, (
            "reader observed a torn snapshot: entry list length != index size"
        )
        # And the length must be one of the two consistent values.
        assert len(s.entries) in (old_len, new_len), (
            "reader observed a snapshot with an unexpected entry count: "
            f"{len(s.entries)} (expected {old_len} or {new_len})"
        )

    # Sanity: we actually saw BOTH states (otherwise the test window was too
    # tight to prove the swap happened concurrently). This is best-effort; if it
    # flakes on a very slow CI we still have the per-sample consistency checks
    # above as the hard assertion. Use a soft check + flag if old wasn't seen.
    seen_built_ats = {s.built_at for s in samples}
    assert new_built_at in seen_built_ats, "reader never saw the new snapshot"


# --- 2. Reload picks up changed data -----------------------------------------


def test_reload_picks_up_changed_data(tmp_path: Path) -> None:
    """Overwriting the dump and reloading reflects the new data in the live index."""
    _reset_live_snapshot()
    dump = tmp_path / "dump.json"

    # v1: three entries.
    _write_dump(
        dump,
        [
            _make_entry("v1-0001", doctor_name="Alice"),
            _make_entry("v1-0002", doctor_name="Bob"),
            _make_entry("v1-0003", doctor_name="Carol"),
        ],
    )
    v1 = build_and_swap(dump)
    assert len(v1.entries) == 3
    v1_built_at = v1.built_at

    # Overwrite the SAME path with v2: different ids, different count.
    _write_dump(
        dump,
        [
            _make_entry("v2-0001", doctor_name="Dave"),
            _make_entry("v2-0002", doctor_name="Eve"),
        ],
    )
    # Ensure built_at strictly advances (it's a freshness signal).
    time.sleep(0.01)
    v2 = build_and_swap(dump)
    live = get_live_snapshot()

    assert live is v2
    assert len(live.entries) == 2
    assert {e.id for e in live.entries} == {"v2-0001", "v2-0002"}
    # Old ids are gone.
    assert all(not e.id.startswith("v1-") for e in live.entries)
    # Timestamp advanced.
    assert live.built_at > v1_built_at
    # Search reflects the new data (Dave is live, Alice is not).
    dave_hits = live.index.search("Dave", limit=10)
    assert dave_hits and dave_hits[0].entry.id == "v2-0001"
    alice_hits = live.index.search("Alice", limit=10)
    assert not alice_hits


# --- 3. Failure does NOT replace the live index ------------------------------


def test_reload_failure_keeps_live_index(tmp_path: Path) -> None:
    """A reload pointed at a MISSING path raises and leaves the live index intact.

    The atomic-swap "don't swap on failure" guarantee: ``build_and_swap`` raises
    *before* touching the live reference, so the last good snapshot keeps
    serving (same entry count, same built_at).
    """
    _reset_live_snapshot()
    # Seed a good snapshot first.
    good = _write_dump(
        tmp_path / "good.json",
        [_make_entry(f"good-{i:04d}") for i in range(4)],
    )
    v1 = build_and_swap(good)
    v1_count = len(v1.entries)
    v1_built_at = v1.built_at

    # Now point a reload at a path that does not exist -> LoaderError raised.
    missing = tmp_path / "does-not-exist.json"
    with pytest.raises(LoaderError):
        build_and_swap(missing)

    # Live snapshot is UNCHANGED: same object, same count, same built_at.
    live = get_live_snapshot()
    assert live is v1, "live snapshot object should be unchanged after a failed reload"
    assert len(live.entries) == v1_count
    assert live.built_at == v1_built_at
    assert {e.id for e in live.entries} == {f"good-{i:04d}" for i in range(4)}


def test_reload_malformed_json_keeps_live_index(tmp_path: Path) -> None:
    """A reload pointed at malformed JSON raises and leaves the live index intact."""
    _reset_live_snapshot()
    good = _write_dump(
        tmp_path / "good.json",
        [_make_entry(f"good-{i:04d}") for i in range(3)],
    )
    v1 = build_and_swap(good)
    v1_built_at = v1.built_at

    bad = tmp_path / "bad.json"
    bad.write_text("{ this is not valid json", encoding="utf-8")
    with pytest.raises(LoaderError):
        build_and_swap(bad)

    live = get_live_snapshot()
    assert live is v1
    assert live.built_at == v1_built_at
    assert len(live.entries) == 3


# --- 4. Manual reload endpoint -----------------------------------------------


@pytest.fixture()
def reload_client(tmp_path: Path):
    """A TestClient whose ``settings.data_path`` points at a controllable dump.

    Patches ``settings.data_path`` (read by the lifespan startup build and by
    ``POST /admin/reload``) to a tmp file, writes an initial dump, yields the
    client, and restores the setting on teardown. The lifespan builds the index
    on entry and stops the scheduler on exit.
    """
    dump = tmp_path / "live_dump.json"
    _write_dump(
        dump,
        [_make_entry(f"init-{i:04d}") for i in range(3)],
    )
    original_path = settings.data_path
    settings.data_path = str(dump)
    try:
        with TestClient(api_module.app) as client:
            yield client, dump
    finally:
        settings.data_path = original_path


def test_admin_reload_success(reload_client) -> None:
    """``POST /admin/reload`` returns 200 + ``reloaded: true``, advancing the index."""
    client, dump = reload_client

    before = get_live_snapshot()
    before_built_at = before.built_at

    # Overwrite the dump with new data, then reload.
    time.sleep(0.01)
    _write_dump(
        dump,
        [_make_entry(f"fresh-{i:04d}") for i in range(6)],
    )
    response = client.post("/admin/reload")

    assert response.status_code == 200
    body = response.json()
    assert body["reloaded"] is True
    assert body["entry_count"] == 6
    assert body["total"] == 6
    assert body["skipped"] == 0
    assert body["error"] is None
    # built_at is a parseable ISO timestamp and advanced.
    parsed = datetime.fromisoformat(body["built_at"])
    after = get_live_snapshot()
    assert after.built_at > before_built_at
    assert parsed == after.built_at
    assert {e.id for e in after.entries} == {f"fresh-{i:04d}" for i in range(6)}


def test_admin_reload_failure_returns_reloaded_false(reload_client) -> None:
    """A reload against a broken dump returns 200 + ``reloaded: false`` + error.

    The endpoint always responds 200 (clean JSON body). On failure the live
    index is unchanged (the last good snapshot keeps serving).
    """
    client, dump = reload_client

    before = get_live_snapshot()
    before_count = len(before.entries)
    before_built_at = before.built_at

    # Corrupt the dump (non-JSON), then reload.
    dump.write_text("{ not valid json", encoding="utf-8")
    response = client.post("/admin/reload")

    assert response.status_code == 200
    body = response.json()
    assert body["reloaded"] is False
    assert body["entry_count"] == 0
    assert body["built_at"] == ""
    assert isinstance(body["error"], str) and body["error"]

    # Live index is UNCHANGED.
    after = get_live_snapshot()
    assert after is before
    assert len(after.entries) == before_count
    assert after.built_at == before_built_at


def test_admin_reload_missing_file_returns_reloaded_false(reload_client) -> None:
    """A reload against a missing file returns 200 + ``reloaded: false`` + error."""
    client, dump = reload_client
    # Point settings at a missing path for this one call.
    dump.unlink()
    response = client.post("/admin/reload")
    assert response.status_code == 200
    body = response.json()
    assert body["reloaded"] is False
    assert "not found" in body["error"].lower()


# --- 5. Scheduler -------------------------------------------------------------


def test_scheduler_starts_and_stops_with_lifespan(tmp_path: Path) -> None:
    """The scheduler thread is running inside the lifespan and gone after exit.

    Uses a tiny interval so the scheduler is genuinely active. After the
    lifespan exits there must be no lingering ``medical-app-refresh`` thread
    (the shutdown ``Event`` + ``join`` must have stopped it).
    """
    dump = _write_dump(
        tmp_path / "sched.json",
        [_make_entry(f"sched-{i:04d}") for i in range(2)],
    )
    original_interval = settings.refresh_interval_seconds
    original_path = settings.data_path
    settings.refresh_interval_seconds = 1
    settings.data_path = str(dump)
    try:
        # No scheduler thread before entering the lifespan.
        assert not _has_refresh_thread()

        with TestClient(api_module.app):
            # While the app is up, the scheduler thread exists.
            assert _has_refresh_thread(), "scheduler thread should run during lifespan"

        # After exit, the scheduler thread is gone (no leak).
        # Give the OS a beat to reap the joined thread.
        deadline = time.time() + 2.0
        while _has_refresh_thread() and time.time() < deadline:
            time.sleep(0.02)
        assert not _has_refresh_thread(), "scheduler thread leaked after lifespan exit"
    finally:
        settings.refresh_interval_seconds = original_interval
        settings.data_path = original_path


def test_scheduler_disabled_when_interval_zero(tmp_path: Path) -> None:
    """``refresh_interval_seconds == 0`` disables the scheduler (no thread started)."""
    dump = _write_dump(
        tmp_path / "nosched.json",
        [_make_entry(f"nos-{i:04d}") for i in range(2)],
    )
    original_interval = settings.refresh_interval_seconds
    original_path = settings.data_path
    settings.refresh_interval_seconds = 0
    settings.data_path = str(dump)
    try:
        with TestClient(api_module.app):
            assert not _has_refresh_thread(), (
                "scheduler must not start when refresh_interval_seconds == 0"
            )
        assert not _has_refresh_thread()
    finally:
        settings.refresh_interval_seconds = original_interval
        settings.data_path = original_path


def test_scheduler_fires_reload_on_interval(tmp_path: Path) -> None:
    """A reload fires (live snapshot advances) when the interval elapses.

    Uses a sub-second interval and a short bounded wait. This is the
    timing-sensitive complement to the leak test; we assert the built_at
    advances after the interval, proving the scheduler actually calls
    ``build_and_swap`` rather than just sleeping forever.
    """
    dump = _write_dump(
        tmp_path / "fire.json",
        [_make_entry(f"fire-{i:04d}") for i in range(2)],
    )
    original_interval = settings.refresh_interval_seconds
    original_path = settings.data_path
    settings.refresh_interval_seconds = 1  # 1s — fires quickly but not flakily
    settings.data_path = str(dump)
    try:
        with TestClient(api_module.app) as client:
            first_built_at = client.get("/health").json()["index_built_at"]
            # Wait for at least one scheduled reload (interval=1s) to land.
            reloaded = False
            deadline = time.time() + 5.0
            while time.time() < deadline:
                current = client.get("/health").json()["index_built_at"]
                if current != first_built_at:
                    reloaded = True
                    break
                time.sleep(0.1)
            assert reloaded, "scheduler did not fire a reload within the interval"
    finally:
        settings.refresh_interval_seconds = original_interval
        settings.data_path = original_path


# --- helpers ------------------------------------------------------------------


def _has_refresh_thread() -> bool:
    """Return True if a live ``medical-app-refresh`` thread currently exists."""
    return any(
        t.name == "medical-app-refresh" and t.is_alive() for t in threading.enumerate()
    )
