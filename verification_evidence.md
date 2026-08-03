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
| | | | |

## feat-003 — JSON Loader / Data Pipeline
| Date | Check | Command | Result |
|---|---|---|---|
| | | | |

## feat-004 — In-Memory Fuzzy Search Index
| Date | Check | Command | Result |
|---|---|---|---|
| | | | |

## feat-005 — FastAPI Search & Read Endpoints
| Date | Check | Command | Result |
|---|---|---|---|
| | | | |

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
