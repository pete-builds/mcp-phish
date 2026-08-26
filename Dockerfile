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
#
# This already happened once: a Dependabot *digest* bump carried the tag from
# 3.13 to 3.14 and shipped Python 3.14.7 to production. A digest is opaque, so
# the tag beside it gets edited without reading as a version change. CI now
# asserts the built image's Python minor version (see the build-image job in
# .github/workflows/ci.yml), so that failure mode is loud rather than silent.
# If Dependabot proposes a FROM line whose tag is not 3.13, close the PR.
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS builder

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
# Same pin as the builder stage. Keep both stages on the identical tag+digest.
FROM python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/site-packages \
    PATH=/app/site-packages/bin:$PATH

# Apply current Debian security updates on top of the pinned Python base image.
#
# The ADD is not decoration and must stay directly above the RUN. CI builds with
# `cache-from: type=gha`, and this RUN's cache key is just its command string,
# which never changes -- so buildkit served this layer from cache indefinitely and
# the "current security updates" above were whatever was current the day the layer
# was FIRST built. Verified on 2026-08-26: the build logged `#11 CACHED` while the
# image still shipped libssl3t64 3.5.6-1~deb13u2, three weeks after
# 3.5.7-1~deb13u2 (CVE-2026-14456) had landed in trixie-security. A layer that
# claims to apply security updates and silently does not is worse than not having
# it, because the Trivy gate then fails with nothing in the repo to change.
#
# trixie-security's Release file changes whenever a security update is published,
# so ADDing it makes buildkit invalidate this layer exactly when there is
# something new to install, and only then.
ADD https://deb.debian.org/debian-security/dists/trixie-security/Release /tmp/debian-security-release
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /tmp/debian-security-release /var/lib/apt/lists/*

# Non-root user with pinned UID 1000 (no shell, no home).
RUN groupadd --system --gid 1000 mcp \
    && useradd --system --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin mcp \
    && mkdir -p /data \
    && chown -R mcp:mcp /data

# Drop pip from the runtime image. Nothing at runtime uses it: dependencies are
# built into /wheels in the builder stage and reach this stage via PYTHONPATH,
# and both the entrypoint and the healthcheck are plain `python -m` calls.
#
# This is also the only fix for two recurring Trivy HIGHs. pip ships a vendored
# dependency set (see pip/_vendor/vendor.txt) that Trivy scans as real packages:
# msgpack 1.1.2 (GHSA-6v7p-g79w-8964) and setuptools 70.3.0 (CVE-2025-47273).
# Neither appears in requirements.lock, so no lockfile regeneration can move
# them, and pip 26.2.1 is already the latest release. Removing the unused
# component is the fix; pinning around it is not possible.
RUN python -m pip uninstall -y pip \
    && rm -rf /usr/local/lib/python3.*/site-packages/pip \
              /usr/local/lib/python3.*/site-packages/pip-*.dist-info \
              /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.*

WORKDIR /app
COPY --from=builder /wheels /app/site-packages
RUN chown -R mcp:mcp /app

USER mcp

EXPOSE 3705

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD ["python", "-m", "mcp_phish.healthcheck"]

ENTRYPOINT ["python", "-m", "mcp_phish.server"]
