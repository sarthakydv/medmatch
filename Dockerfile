# Multi-stage build for the Medical Entries Data Pipeline & Search API.
# Keeps the runtime image lean: only the runtime venv + the package + the
# synthetic mock data. Dev deps (pytest, ruff, mypy) stay out of the final image.

# ----------------------------------------------------------------------------
# Stage 1: builder — create an isolated venv and install runtime requirements.
# ----------------------------------------------------------------------------
FROM python:3.12-slim AS builder

# Build-time-only tools for compiling any C-extension wheels (e.g. rapidfuzz,
# pydantic-core). Removed from the final stage by virtue of multi-stage build.
RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential \
    && rm -rf /var/lib/apt/lists/*

# Build the venv at a fixed, well-known path so stage 2 can copy it verbatim.
RUN python -m venv /opt/venv
# Make the venv's binaries the default for subsequent RUN commands.
ENV PATH="/opt/venv/bin:${PATH}"

# Install ONLY runtime deps (no dev tooling) and keep pip current.
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ----------------------------------------------------------------------------
# Stage 2: runtime — copy the venv + app, run as a non-root user.
# ----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Unbuffered stdout/stderr so logs (uvicorn, structured logging) appear in
# real time; never write .pyc files to keep the image clean.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}" \
    # Default to the synthetic dump baked into the image. Override at runtime
    # (e.g. via docker-compose) with MEDICAL_DATA_PATH to point at real data.
    MEDICAL_DATA_PATH=/app/data/mock_entries.json \
    MEDICAL_HOST=0.0.0.0 \
    MEDICAL_PORT=8000

WORKDIR /app

# Copy the venv built in stage 1.
COPY --from=builder /opt/venv /opt/venv

# Create a dedicated non-root user and grant it the working dir.
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app

# Copy the application package and the synthetic data. The default data path
# (MEDICAL_DATA_PATH above) points at this file.
COPY --chown=appuser:appuser medical_app/ ./medical_app/
COPY --chown=appuser:appuser data/mock_entries.json ./data/mock_entries.json

USER appuser

# Expose the default API port (MEDICAL_PORT). Compose maps 8000:8000.
EXPOSE 8000

# Run via the package's main() so host/port/log_level come from settings
# (honoring all MEDICAL_* env vars, incl. setup_logging + log_level).
CMD ["python", "-m", "medical_app.main"]
