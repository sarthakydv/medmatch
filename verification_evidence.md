# Verification Evidence

This is the **single source of truth for verification evidence**. `feature_list.json` tracks
only status; all command-and-output proof that a feature is actually done lives here.

When a feature reaches `done`, append a row to the relevant table with the exact command(s)
run and their output (or a faithful summary). Never mark a feature `done` in `feature_list.json`
without a corresponding entry here.

## How to record evidence

- One section per feature, in order.
- Include the **command** and the **result** (exit code 0 / pass, or the key output lines).
- For test runs, paste the summary line (e.g. `4 passed in 0.12s`).
- If a check is genuinely N/A for a feature, say so explicitly rather than leaving it blank.

---

## feat-001 — Project Bootstrap
| Date | Check | Command | Result |
|---|---|---|---|
| 2026-08-03 | Fresh-checkout gate | `env -u PYTHON ./init.sh` (clean copy, no `.PYTHON`/`.venv`) | exit 0 — created venv, installed runtime+dev deps, `compileall` clean, `1 passed in 0.01s`, ruff check/format pass, `mypy: Success: no issues found in 8 source files`, "Verification Complete" |
| 2026-08-03 | In-repo gate | `./init.sh` | exit 0 — all checks pass (same suite as above) |
| 2026-08-03 | init.sh bug fix | edited `init.sh:24` `"$PYTHON"` → `"$PY"` | Fresh-checkout run previously failed `./init.sh: line 24: : command not found` (exit 127); now exits 0. Required for feat-001's "fresh checkout passes ./init.sh with no manual steps" goal. |

**Artifacts created:** `requirements.txt` (fastapi, uvicorn[standard], rapidfuzz, pydantic), `requirements-dev.txt` (pytest, ruff, mypy, httpx), `pyproject.toml` (ruff py311 E/F/I/W/UP/B; mypy strict), `medical_app/` package (8 typed stub modules), `tests/` (smoke test).

## feat-002 — Mock Data Set & Schema Definition
| Date | Check | Command | Result |
|---|---|---|---|
| 2026-08-03 | Verification gate | `./init.sh` | exit 0 — `12 passed in 0.05s` (smoke + model tests), ruff check/format clean, `mypy: Success: no issues found in 8 source files` |
| 2026-08-03 | Data sanity (independent) | `python3 -c "..."` over `data/mock_entries.json` | count: 73; all dicts; unique ids; `has Dobert (doctor_name): True`; `has Bukalest (location): True`; all parse via `MedicalEntry`; entries with missing/empty required fields: `[]` |

**Artifacts:** `medical_app/models.py` — Pydantic v2 `MedicalEntry` (required `id`/`doctor_name`/`location` with non-empty validators; optional `specialty`/`facility`/`phone`/`notes`; `extra="allow"`, `str_strip_whitespace=True`). `data/mock_entries.json` — 73 synthetic entries (no PHI) with intentional typos (`Dobert`↔`Robert`, `Bukalest`↔`Bucharest`, plus ~12 more doctor-name and ~15 more location typos), varying optional fields and specialties. `tests/test_models.py` — 10 model validation cases.

## feat-003 — JSON Loader / Data Pipeline
| Date | Check | Command | Result |
|---|---|---|---|
| 2026-08-03 | Verification gate | `./init.sh` | exit 0 — `33 passed in 0.13s` (16 loader + 10 model + 2 smoke + parametrized), ruff check/format clean, `mypy: Success: no issues found in 8 source files` |
| 2026-08-03 | Module entrypoint | `python3 -m medical_app.loader` | `Loaded 73/73 entries from data/mock_entries.json (skipped 0).` |
| 2026-08-03 | Resilience (manual) | tmp JSON: 2 valid + empty doctor_name + empty id + dup id | `entries loaded: 2 / total: 5 / skipped: 3`, ids kept `['a','d']`; each skip logged; load did not abort |
| 2026-08-03 | Error cases (manual) | missing path / empty file | missing file → `raised LoaderError`; empty file → `loaded: 0 skipped: 0` |

**Artifacts:** `medical_app/loader.py` — `load_entries(path)` validating against `MedicalEntry`, enforcing `id` uniqueness; frozen+slotted `LoadResult(entries, skipped, total)` with derived `loaded`; `LoaderError` for structural errors (missing file, unreadable, malformed JSON, top-level not a list); malformed entries & duplicate ids skipped+counted+logged (resilience over strictness); empty file / `[]` → valid empty result; `normalized_text(entry)` lowercased searchable text; `__main__` entrypoint. `tests/test_loader.py` — 16 tests covering happy path, malformed-entry rejection (skip+count, no abort), missing file, empty file, `[]`, non-list JSON, malformed JSON, duplicate id.

## feat-004 — In-Memory Fuzzy Search Index
| Date | Check | Command | Result |
|---|---|---|---|
| 2026-08-03 | Verification gate | `./init.sh` | exit 0 — `60 passed in 0.10s` (27 index + 16 loader + 10 model + 2 smoke + parametrized), ruff check/format clean, `mypy: Success: no issues found in 8 source files` |
| 2026-08-03 | Critical typo tests | `pytest tests/test_index.py::test_bukalest_matches_bucharest test_dobert_matches_robert -v` | `2 passed` |
| 2026-08-03 | Typo-bridging (independent) | `SearchIndex(load_entries(...).entries).search("Bukalest"/"Dobert")` | `Bukalest` → Bucharest entries (ent-0002/0003/0005 @ 70.59); `Dobert` → Robert doctors (ent-0003/0004/0006 @ 83.33) |

**Artifacts:** `medical_app/index.py` — `SearchIndex(entries, *, threshold=70.0)` indexing lowercased `doctor_name`/`location`/`specialty`; `search(query, *, field=None, limit=10, threshold=None) -> list[SearchResult]`; scorer `max(fuzz.partial_ratio, fuzz.token_sort_ratio)` (handles both substring + transposition typos); ranking by `(-score, entry.id)` for determinism; empty query → `[]`; O(N) build for cheap daily rebuild; immutable instance suitable for atomic swap. `SearchResult` frozen+slotted dataclass (`entry`, `score`, `field`). `tests/test_index.py` — 27 tests incl. the required Bukalest→Bucharest and Dobert→Robert bridging, plus ranking/threshold/field/limit/determinism/empty-query cases.

## feat-005 — FastAPI Search & Read Endpoints
| Date | Check | Command | Result |
|---|---|---|---|
| 2026-08-03 | Verification gate | `./init.sh` | exit 0 — `89 passed` (29 api + 27 index + 16 loader + 10 model + 2 smoke), ruff check/format clean, `mypy: Success: no issues found in 9 source files` |
| 2026-08-03 | End-to-end (TestClient, lifespan) | `with TestClient(app) as c: c.get(...)` | `/health` → `{status:ok, index_built_at:<ISO UTC>, entry_count:73}`; `/search?q=Bukalest` → Bucharest hits ent-0002/0003/0005 @70.6; `/search?q=Dobert` → Robert hits ent-0003/0004/0006 @83.3; `/entries/ent-0001` → 200; `/entries/nope` → 404; `/entries?limit=3` → ids ent-0001/0002/0003; unknown `field` → 422 |

**Artifacts:** `medical_app/schemas.py` (new) — Pydantic v2 response models (`EntryOut`, `SearchHit`, `SearchResponse`, `EntriesListResponse`, `HealthResponse`, `ErrorResponse`). `medical_app/api.py` — four endpoints (`GET /health`, `GET /entries?limit=&offset=`, `GET /entries/{id}`, `GET /search?q=&field=&limit=`) with `response_model` annotations + a `lifespan` handler that builds the `SearchIndex` once at startup from `settings.data_path`. `medical_app/service.py` — minimal `IndexSnapshot(index, entries, built_at)` + `get_live_snapshot()`/`set_live_snapshot()` (single assignment; atomic-swap/scheduler deferred to feat-006). `tests/test_api.py` — 29 TestClient tests incl. the PRODUCT.md Bukalest→Bucharest and Dobert→Robert success criteria, pagination, 404, and 422 validation.

## feat-006 — Atomic Daily Refresh
| Date | Check | Command | Result |
|---|---|---|---|
| | | | |

## feat-007 — Configuration, Logging & Resilience
| Date | Check | Command | Result |
|---|---|---|---|
| | | | |

## feat-008 — Containerization & Run Instructions
| Date | Check | Command | Result |
|---|---|---|---|
| | | | |

## feat-009 — Final Verification & Handoff
| Date | Check | Command | Result |
|---|---|---|---|
| | | | |
