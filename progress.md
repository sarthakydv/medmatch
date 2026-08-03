# Session Progress Log

## Current State

**Last Updated:** 2026-08-03
**Session ID:** harness-bootstrap
**Active Feature:** (none yet — harness only; no implementation started)

## Status

### What's Done

- [x] Initialized git repository (`git init`)
- [x] Created the agent harness using the harness-creator skill
- [x] Customized `AGENTS.md`, `feature_list.json`, `progress.md`, `session-handoff.md`, `init.sh` for this Python project
- [x] Wrote `docs/PRODUCT.md`, `docs/ARCHITECTURE.md`, `docs/DATA_SCHEMA.md`
- [x] Added `verification_evidence.md` (evidence kept separate from feature status)
- [x] Added `.gitignore`
- [x] Validated the harness with `validate-harness.mjs` (see `verification_evidence.md`)

### What's In Progress

- [ ] (nothing — handoff point)

### What's Next

1. User reviews and commits the harness.
2. New session begins: read `AGENTS.md`, run `./init.sh`, read `feature_list.json`.
3. Implement **feat-001 (Project Bootstrap)** first — it has no dependencies.
4. Proceed feature-by-feature per the dependency chain in `feature_list.json`, ideally using subagents (one feature per subagent where the work is independent).

## Blockers / Risks

- [ ] **No committed history yet**: harness is uncommitted pending user review. Next session should start from the user's commit.
- [ ] **`./init.sh` exits non-zero on a clean machine today**: there is no `requirements.txt`/venv until feat-001, so `pytest` is not installed and the pytest step fails (exit 1). The harness validates 100/100 structurally, but the *runtime* gate only goes green once feat-001 creates `requirements.txt` + dev deps. Flagged for review: either accept this (green-from-feat-001 onward) or soften the pytest step to skip when pytest is absent.
- [ ] **Real client data**: only synthetic mock data should ever be committed. `.gitignore` excludes a `data/raw/` directory for any real dumps; only `data/mock_entries.json` is tracked.

## Decisions Made

- **Stack: Python 3.11+ / FastAPI**
  - Context: problem asks for a fast, low-maintenance API over infrequently-updated data.
  - Alternatives: Node/Express (fine, but Python ecosystem + rapidfuzz is a strong fit for fuzzy search); Go (faster but more code for fuzzy matching).
- **Fuzzy search via rapidfuzz (Levenshtein), in-memory**
  - Context: transcription errors described are simple typos (edit distance 1–2); data is small and updated daily, so it fits in memory and a separate search server is overkill.
  - Alternatives: Elasticsearch/Meilisearch (heavy, needs a second process to maintain — violates "low maintenance"); pg_trgm in Postgres (adds a DB the problem doesn't require).
- **No database**
  - Context: client provides a full daily JSON dump; nothing else needs persistence. Keeping the index in process memory keeps searches sub-millisecond and removes a failure surface.
- **Atomic index swap for daily refresh**
  - Context: must serve fast and never show partial data during a reload. Build the new index fully, then swap the reference.

## Files Modified This Session

- `AGENTS.md` — customized for Python/FastAPI medical search project
- `feature_list.json` — 9 features covering pipeline + API + refresh + ops (status only)
- `verification_evidence.md` — dedicated home for verification evidence (separate from status)
- `progress.md` — this file
- `session-handoff.md` — handoff for the next session
- `init.sh` — Python verification entrypoint (venv, compileall, pytest, ruff, mypy)
- `docs/PRODUCT.md` — problem statement and requirements
- `docs/ARCHITECTURE.md` — component design and decisions
- `docs/DATA_SCHEMA.md` — medical entry schema
- `.gitignore` — Python + venv + real-data exclusions

## Evidence of Completion

- [x] Harness validates: see `verification_evidence.md` (feat-001 section will hold the harness-validation row once recorded; the validate-harness run is the current proof).
- [ ] (Implementation evidence will accumulate in `verification_evidence.md` as each feature is completed.)

## Notes for Next Session

- This session created ONLY the harness. No source code, no `requirements.txt`, no package yet — that's feat-001.
- Start by running `./init.sh`. It will report that there's nothing to install/compile/test yet; that's expected and it should still exit 0.
- Then implement feat-001 (create the `medical_app/` package, `requirements.txt`, `requirements-dev.txt`), re-run `./init.sh`, and mark feat-001 done with evidence.
- Subagent usage: each feature in `feature_list.json` is a self-contained unit. Launch one subagent per feature, giving it the feature description and pointing it at `AGENTS.md` + the relevant `docs/`. Features must be done in dependency order.
