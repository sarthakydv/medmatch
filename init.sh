#!/bin/bash
set -e

# Medical Entries Data Pipeline & Search API — verification entrypoint.
# Runs dependency setup + all checks. Fails fast on the first error.

cd "$(dirname "$0")"

PY=${PYTHON:-python3}

echo "=== Harness Initialization ==="

# --- Environment setup -------------------------------------------------------
if [ -d .venv ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PY=python
fi

# Create a venv on first run so dev tooling (ruff, mypy) is available, then
# install requirements. Skipped if requirements files are absent (early bootstrap).
if [ ! -d .venv ] && [ -f requirements.txt ]; then
  echo "=== Creating virtualenv (.venv) ==="
  "$PY" -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  PY=python
fi

if [ -f requirements.txt ]; then
  echo "=== Installing runtime requirements ==="
  $PY -m pip install -q --upgrade pip
  $PY -m pip install -q -r requirements.txt
fi
if [ -f requirements-dev.txt ]; then
  echo "=== Installing dev requirements ==="
  $PY -m pip install -q -r requirements-dev.txt
fi

# --- Compile (syntax check) --------------------------------------------------
if [ -d medical_app ]; then
  echo "=== compileall ==="
  $PY -m compileall -q medical_app
fi

# --- Tests -------------------------------------------------------------------
# pytest exits 5 when it collects zero tests. Early on there are no tests yet,
# so treat "no tests collected" as success rather than failing the gate.
echo "=== pytest ==="
$PY -m pytest -q || code=$?
if [ "${code:-0}" -ne 0 ] && [ "${code:-0}" -ne 5 ]; then
  echo "pytest failed with exit code ${code}"
  exit "${code}"
fi

# --- Lint + format + type check (dev tooling, best-effort) -------------------
if $PY -m ruff --version >/dev/null 2>&1; then
  echo "=== ruff check ==="
  $PY -m ruff check .
  echo "=== ruff format --check ==="
  $PY -m ruff format --check .
else
  echo "=== ruff not installed; skipping lint/format (install requirements-dev.txt) ==="
fi

if $PY -m mypy --version >/dev/null 2>&1; then
  if [ -d medical_app ]; then
    echo "=== mypy ==="
    $PY -m mypy medical_app || echo "(mypy: best-effort, non-blocking)"
  fi
else
  echo "=== mypy not installed; skipping type check (install requirements-dev.txt) ==="
fi

echo "=== Verification Complete ==="
echo ""
echo "Next steps:"
echo "1. Read feature_list.json to see current feature state"
echo "2. Pick ONE unfinished feature whose dependencies are all 'done'"
echo "3. Implement only that feature"
echo "4. Re-run ./init.sh before claiming done"
