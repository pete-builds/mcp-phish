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
the upstream rate limits. A single TTL governs every entry. Expired rows are
filtered on read and deleted on write: ``init()`` sweeps once, and ``put()``
sweeps every ``evict_every`` writes. Nothing background-runs.
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

    def __init__(self, db_path: str, ttl_seconds: int, evict_every: int = 100) -> None:
        self.db_path = db_path
        self.ttl_seconds = ttl_seconds
        self.evict_every = max(1, evict_every)
        self.last_hit_ts: float | None = None
        self.last_miss_ts: float | None = None
        self._writes_since_evict = 0

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
        await self.evict_expired()

    async def evict_expired(self) -> int:
        """Delete every row older than the TTL. Returns the number deleted.

        Reads already ignore expired rows, so this only reclaims disk: without
        it, every distinct query ever made stays in the file forever.
        """
        cutoff = int(time.time()) - self.ttl_seconds
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM cache WHERE fetched_at < ?", (cutoff,))
            await db.commit()
            deleted = int(cursor.rowcount)
        self._writes_since_evict = 0
        if deleted:
            logger.debug("cache evicted expired rows", extra={"deleted": deleted})
        return deleted

    async def get(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        ttl_override: int | None = None,
    ) -> Any | None:
        """Return parsed JSON if a fresh entry exists, else None.

        ``ttl_override`` lets a single call use a shorter (or longer) freshness
        window than the instance default. The hot-window read path passes a
        small override so frequent polls of an in-progress show see upstream
        updates within a couple of minutes instead of being pinned to the
        24h default. The stored entry is untouched; only the freshness cutoff
        for this read changes.
        """
        params_hash = _hash_params(dict(params))
        ttl = self.ttl_seconds if ttl_override is None else ttl_override
        cutoff = int(time.time()) - ttl
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
        self._writes_since_evict += 1
        if self._writes_since_evict >= self.evict_every:
            await self.evict_expired()

    def size_bytes(self) -> int:
        """Best-effort current DB file size. Returns 0 if the file isn't there yet."""
        try:
            return os.path.getsize(self.db_path)
        except OSError:
            return 0
