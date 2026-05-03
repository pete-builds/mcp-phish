"""Opaque KV cache for upstream API responses.

Schema is intentionally minimal:

```
CREATE TABLE IF NOT EXISTS cache (
    endpoint     TEXT NOT NULL,
    params_hash  TEXT NOT NULL,
    raw_json     TEXT NOT NULL,
    fetched_at   INTEGER NOT NULL,
    PRIMARY KEY (endpoint, params_hash)
);
```

This is *not* a vault embryo. The Phase 2 Postgres vault is a separate,
normalized store with its own schema. This cache only exists to keep us under
the upstream rate limits. A single TTL governs every entry. Eviction is
opportunistic on read; nothing background-runs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger("mcp_phish.cache")


def _hash_params(params: Mapping[str, Any]) -> str:
    """Deterministic SHA-256 of the JSON-canonicalized params dict."""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ResponseCache:
    """Thin async wrapper over aiosqlite for the opaque KV cache.

    Holds onto the last hit/miss timestamps so ``health()`` can surface them
    without an extra round-trip to the database.
    """

    def __init__(self, db_path: str, ttl_seconds: int) -> None:
        self.db_path = db_path
        self.ttl_seconds = ttl_seconds
        self.last_hit_ts: float | None = None
        self.last_miss_ts: float | None = None

    async def init(self) -> None:
        """Create the parent dir + table on first use. Safe to call repeatedly."""
        parent = Path(self.db_path).parent
        if str(parent) and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS cache (
                    endpoint     TEXT NOT NULL,
                    params_hash  TEXT NOT NULL,
                    raw_json     TEXT NOT NULL,
                    fetched_at   INTEGER NOT NULL,
                    PRIMARY KEY (endpoint, params_hash)
                )
                """
            )
            await db.commit()

    async def get(self, endpoint: str, params: Mapping[str, Any]) -> Any | None:
        """Return parsed JSON if a fresh entry exists, else None."""
        params_hash = _hash_params(dict(params))
        cutoff = int(time.time()) - self.ttl_seconds
        async with (
            aiosqlite.connect(self.db_path) as db,
            db.execute(
                "SELECT raw_json, fetched_at FROM cache "
                "WHERE endpoint = ? AND params_hash = ? AND fetched_at >= ?",
                (endpoint, params_hash, cutoff),
            ) as cursor,
        ):
            row = await cursor.fetchone()
        if row is None:
            self.last_miss_ts = time.time()
            return None
        self.last_hit_ts = time.time()
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            logger.warning("cache row had invalid JSON, treating as miss")
            self.last_miss_ts = time.time()
            return None

    async def put(self, endpoint: str, params: Mapping[str, Any], payload: Any) -> None:
        """Insert or replace a row."""
        params_hash = _hash_params(dict(params))
        raw_json = json.dumps(payload, separators=(",", ":"), default=str)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO cache "
                "(endpoint, params_hash, raw_json, fetched_at) VALUES (?, ?, ?, ?)",
                (endpoint, params_hash, raw_json, int(time.time())),
            )
            await db.commit()

    def size_bytes(self) -> int:
        """Best-effort current DB file size. Returns 0 if the file isn't there yet."""
        try:
            return os.path.getsize(self.db_path)
        except OSError:
            return 0
