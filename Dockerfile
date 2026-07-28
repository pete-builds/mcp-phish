# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Builder stage: compile wheels from the hash-locked requirements.
# ---------------------------------------------------------------------------
# Pinned by digest so rebuilds are reproducible. Refresh with:
#   docker pull python:3.13-slim
#   docker inspect python:3.13-slim --format '{{index .RepoDigests 0}}'
# Dependabot keeps the digest fresh weekly via .github/dependabot.yml.
#
# The TAG must stay 3.13: pyproject.toml (requires-python, mypy python_version,
# ruff target-version), the CI matrix, and requirements*.lock (compiled with
# --python-version 3.13) all target 3.13. Moving the tag alone silently ships a
# runtime that no lockfile or check ever exercised. Retarget all of them
# together or not at all.
FROM python:3.13-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91 AS builder

WORKDIR /build

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Build minimal wheels into /wheels so the runtime stage can install offline.
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes --target /wheels -r requirements.lock

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir --target /wheels --no-deps .

# ---------------------------------------------------------------------------
# Runtime stage: slim image with only the installed package + UID 1000 user.
# ---------------------------------------------------------------------------
FROM python:3.13-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91 AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/site-packages \
    PATH=/app/site-packages/bin:$PATH

# Apply current Debian security updates on top of the pinned Python base image.
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

# Non-root user with pinned UID 1000 (no shell, no home).
RUN groupadd --system --gid 1000 mcp \
    && useradd --system --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin mcp \
    && mkdir -p /data \
    && chown -R mcp:mcp /data

WORKDIR /app
COPY --from=builder /wheels /app/site-packages
RUN chown -R mcp:mcp /app

USER mcp

EXPOSE 3705

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD ["python", "-m", "mcp_phish.healthcheck"]

ENTRYPOINT ["python", "-m", "mcp_phish.server"]
