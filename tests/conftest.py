"""Shared fixtures for the mcp-phish test suite."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Iterator

import pytest

from mcp_phish.cache import ResponseCache
from mcp_phish.config import Settings

_ENV_VARS = (
    "STUB_MODE",
    "PHISHNET_API_KEY",
    "PHISHIN_API_KEY",
    "PHISHNET_BASE_URL",
    "PHISHIN_BASE_URL",
    "CACHE_DB_PATH",
    "CACHE_TTL_SECONDS",
    "THROTTLE_PHISHNET_RPS",
    "THROTTLE_PHISHIN_RPS",
    "MCP_HOST",
    "MCP_PORT",
    "LOG_LEVEL",
    "LOG_FORMAT",
)


@pytest.fixture
def temp_cache_path() -> Iterator[str]:
    """Disposable file path for an aiosqlite cache."""
    fd, path = tempfile.mkstemp(prefix="phish-cache-test-", suffix=".db")
    os.close(fd)
    os.remove(path)  # let aiosqlite create it cleanly
    try:
        yield path
    finally:
        for suffix in ("", "-journal", "-wal", "-shm"):
            with contextlib.suppress(FileNotFoundError):
                os.remove(path + suffix)


@pytest.fixture
def stub_settings(monkeypatch: pytest.MonkeyPatch, temp_cache_path: str) -> Settings:
    """Stub-mode settings that don't pick up a developer's local .env."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return Settings(
        stub_mode=True,
        log_format="text",
        cache_db_path=temp_cache_path,
        cache_ttl_seconds=86400,
    )


@pytest.fixture
def real_settings(monkeypatch: pytest.MonkeyPatch, temp_cache_path: str) -> Settings:
    """Real-mode settings pointing at fake hosts for HTTP mocking."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return Settings(
        stub_mode=False,
        phishnet_api_key="test-pn-key",
        phishin_api_key="",
        phishnet_base_url="https://api.phish.test/v5",
        phishin_base_url="https://phish.test/api/v2",
        log_format="text",
        cache_db_path=temp_cache_path,
        cache_ttl_seconds=86400,
        throttle_phishnet_rps=50.0,
        throttle_phishin_rps=50.0,
    )


@pytest.fixture
def empty_cache(temp_cache_path: str) -> ResponseCache:
    """Fresh ResponseCache pointing at a brand-new file."""
    return ResponseCache(db_path=temp_cache_path, ttl_seconds=60)


def parse_tool_response(raw: str) -> dict[str, object]:
    """Helper for tests: parse a tool's JSON-string return value."""
    return json.loads(raw)
