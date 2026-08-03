# AGENTS.md

Harness for reliable agent-assisted development of the **Medical Entries Data Pipeline & Search API**.

This is a Python project. The deliverable is (1) a data pipeline that loads a JSON dump of
medical entries and (2) a FastAPI service that serves and searches that data with
typo-tolerant ("fuzzy") matching.

## Startup Workflow

Before writing code:

1. **Confirm working directory** with `pwd` (must be the project root)
2. **Read this file** completely
3. **Read project docs**: `docs/PRODUCT.md`, `docs/ARCHITECTURE.md`, `docs/DATA_SCHEMA.md`
4. **Activate the venv if present** (`source .venv/bin/activate`) — never install globally
5. **Run `./init.sh`** to verify the environment is healthy
6. **Read `feature_list.json`** to see current feature state
7. **Review recent commits** with `git log --oneline -5`

If baseline verification is failing, repair that first before adding new scope.

## Working Rules

- **One feature at a time**: Pick exactly one `not-started` feature from `feature_list.json` whose `dependencies` are all `done`. Do not start a second feature in the same session.
- **Stay in scope**: Don't modify files unrelated to the current feature. If a change is truly cross-cutting, stop and ask the user.
- **Verify before claiming done**: Run `./init.sh`; a feature is done only when the checks pass AND evidence is recorded in `verification_evidence.md` (see Definition of Done below).
- **Python conventions**: Target Python 3.11+. Runtime deps → `requirements.txt`; test/lint tooling → `requirements-dev.txt`. Use `python3 -m venv` for isolation.
- **Never commit secrets or the client's real JSON dump.** Only the synthetic `data/mock_entries.json` is tracked; real data (`data/raw/`, `*.real.json`) is git-ignored.
- **Leave clean state**: A fresh checkout must run `./init.sh` with no manual steps. Commit only with the user's OK.

## Required Artifacts

- `feature_list.json` — Feature state tracker. **Status only** (the source of truth for what is done).
- `verification_evidence.md` — **Verification evidence** (command + output). Kept separate from status so a feature's "done" claim is always backed by proof here.
- `progress.md` — Session continuity log (What's Done / In Progress / Next / Blockers).
- `session-handoff.md` — Resume-from-here doc for the next session.
- `init.sh` — Standard startup and verification path.
- `docs/PRODUCT.md`, `docs/ARCHITECTURE.md`, `docs/DATA_SCHEMA.md` — Problem, design, and data shape.

## Definition of Done

A feature is done only when ALL of the following are true:

- [ ] Target behavior is implemented and matches the feature description in `feature_list.json`
- [ ] `./init.sh` passes (compile + tests + lint/type checks as applicable)
- [ ] Evidence (command + output) recorded under the feature's section in `verification_evidence.md`
- [ ] No dead code or commented-out blocks left behind
- [ ] Repository remains restartable: a fresh checkout + `./init.sh` works with no manual steps

Then — and only then — set the feature's `status` to `done` in `feature_list.json`.

## End of Session

1. Update `progress.md` (state, blockers, next step) and `session-handoff.md` (resume instructions).
2. Ensure any feature marked `done` this session has evidence in `verification_evidence.md`.
3. Commit with a descriptive message once work is in a safe state — ask the user before committing if unsure.
4. Leave the repo clean enough that the next session runs `./init.sh` immediately.

## Verification Commands

```bash
# Full verification (recommended)
./init.sh
```

`./init.sh` runs, in order, failing fast on the first error:

- `python3 -m compileall -q medical_app` — bytecode compile (syntax check)
- `python3 -m pytest -q` — unit tests (exit code 5 = no tests collected yet, treated as pass)
- `ruff check .` — lint (dev dependency)
- `ruff format --check .` — format check (dev dependency)
- `mypy medical_app` — static type check (dev dependency, best-effort)

`ruff` and `mypy` live in `requirements-dev.txt`; `init.sh` installs dev requirements
automatically on first run. Note: on a clean machine with no `requirements.txt` yet
(before feat-001), `init.sh` skips install and only runs the checks that can run.

## Escalation

- **Architecture decisions** (change the search engine, add a database, async vs sync): Consult `docs/ARCHITECTURE.md` first; if not settled there, ask the user.
- **Unclear requirements**: Check `docs/PRODUCT.md`; if still unclear, ask the user rather than guessing.
- **Repeated test failures**: Update `progress.md`, flag for human review, do not mark the feature done.
- **Scope ambiguity**: Re-read the feature's entry in `feature_list.json` for the definition of done.
- **A need to commit**: Ask the user before committing unless explicitly authorized this session.
