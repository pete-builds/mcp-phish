"""Tests for the opaque KV cache."""

from __future__ import annotations

import asyncio

import aiosqlite
import pytest

from mcp_phish.cache import ResponseCache, _hash_params


def test_hash_params_is_order_invariant() -> None:
    a = _hash_params({"year": 1997, "venue": "MSG"})
    b = _hash_params({"venue": "MSG", "year": 1997})
    assert a == b


def test_hash_params_distinguishes_values() -> None:
    a = _hash_params({"year": 1997})
    b = _hash_params({"year": 1998})
    assert a != b


@pytest.mark.asyncio
async def test_get_returns_none_on_miss(empty_cache: ResponseCache) -> None:
    await empty_cache.init()
    result = await empty_cache.get("phishnet:get_show", {"date": "1995-12-30"})
    assert result is None
    assert empty_cache.last_miss_ts is not None
    assert empty_cache.last_hit_ts is None


@pytest.mark.asyncio
async def test_put_then_get_returns_payload(empty_cache: ResponseCache) -> None:
    payload = {"showid": "1252691618", "venue": "MSG"}
    await empty_cache.init()
    await empty_cache.put("phishnet:get_show", {"date": "1995-12-30"}, payload)
    hit = await empty_cache.get("phishnet:get_show", {"date": "1995-12-30"})
    assert hit == payload
    assert empty_cache.last_hit_ts is not None


@pytest.mark.asyncio
async def test_ttl_expiry(temp_cache_path: str) -> None:
    """Use a 0-second TTL plus a real sleep so the cutoff is strictly after fetched_at."""
    cache = ResponseCache(db_path=temp_cache_path, ttl_seconds=1)
    await cache.init()
    await cache.put("ep", {"k": "v"}, {"hello": "world"})
    # Immediate read = hit.
    assert await cache.get("ep", {"k": "v"}) == {"hello": "world"}
    # Wait long enough that the integer cutoff in get() is past fetched_at.
    await asyncio.sleep(2.5)
    assert await cache.get("ep", {"k": "v"}) is None


@pytest.mark.asyncio
async def test_ttl_override_expires_before_instance_ttl(temp_cache_path: str) -> None:
    """A short ttl_override treats an entry as stale even though the 24h
    instance TTL would still consider it fresh."""
    cache = ResponseCache(db_path=temp_cache_path, ttl_seconds=86400)
    await cache.init()
    await cache.put("ep", {"k": "v"}, {"hello": "world"})
    # Default TTL: still a hit.
    assert await cache.get("ep", {"k": "v"}) == {"hello": "world"}
    # 1s override after a >1s wait: treated as a miss.
    await asyncio.sleep(2.5)
    assert await cache.get("ep", {"k": "v"}, ttl_override=1) is None
    # The default-TTL read is unaffected — same entry is still fresh.
    assert await cache.get("ep", {"k": "v"}) == {"hello": "world"}


@pytest.mark.asyncio
async def test_ttl_override_none_uses_instance_ttl(temp_cache_path: str) -> None:
    """ttl_override=None is identical to the default freshness window."""
    cache = ResponseCache(db_path=temp_cache_path, ttl_seconds=1)
    await cache.init()
    await cache.put("ep", {"k": "v"}, {"v": 1})
    assert await cache.get("ep", {"k": "v"}, ttl_override=None) == {"v": 1}
    await asyncio.sleep(2.5)
    assert await cache.get("ep", {"k": "v"}, ttl_override=None) is None


@pytest.mark.asyncio
async def test_size_bytes_grows_after_put(empty_cache: ResponseCache) -> None:
    """SQLite allocates pages in 4KB chunks; write enough to span more than one."""
    await empty_cache.init()
    initial = empty_cache.size_bytes()
    # 50KB of payload guarantees we exceed the initial single-page allocation.
    await empty_cache.put("ep", {"k": "v"}, {"data": list(range(10_000))})
    after = empty_cache.size_bytes()
    assert after > initial


@pytest.mark.asyncio
async def test_replace_on_duplicate_key(empty_cache: ResponseCache) -> None:
    await empty_cache.init()
    await empty_cache.put("ep", {"k": "v"}, {"v": 1})
    await empty_cache.put("ep", {"k": "v"}, {"v": 2})
    assert await empty_cache.get("ep", {"k": "v"}) == {"v": 2}


# ---------------------------------------------------------------------------
# eviction
# ---------------------------------------------------------------------------


async def _plant_expired_row(db_path: str, key: str = "stale") -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO cache (endpoint, params_hash, raw_json, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            ("ep", key, "{}", 0),
        )
        await db.commit()


async def _row_count(db_path: str) -> int:
    async with aiosqlite.connect(db_path) as db, db.execute("SELECT COUNT(*) FROM cache") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.asyncio
async def test_evict_expired_deletes_only_stale_rows(temp_cache_path: str) -> None:
    cache = ResponseCache(db_path=temp_cache_path, ttl_seconds=60)
    await cache.init()
    await cache.put("ep", {"k": "fresh"}, {"v": 1})
    await _plant_expired_row(temp_cache_path)
    assert await _row_count(temp_cache_path) == 2

    assert await cache.evict_expired() == 1

    assert await _row_count(temp_cache_path) == 1
    assert await cache.get("ep", {"k": "fresh"}) == {"v": 1}


@pytest.mark.asyncio
async def test_init_sweeps_expired_rows(temp_cache_path: str) -> None:
    first = ResponseCache(db_path=temp_cache_path, ttl_seconds=60)
    await first.init()
    await _plant_expired_row(temp_cache_path)
    assert await _row_count(temp_cache_path) == 1

    await ResponseCache(db_path=temp_cache_path, ttl_seconds=60).init()

    assert await _row_count(temp_cache_path) == 0


@pytest.mark.asyncio
async def test_put_sweeps_every_n_writes(temp_cache_path: str) -> None:
    cache = ResponseCache(db_path=temp_cache_path, ttl_seconds=60, evict_every=2)
    await cache.init()
    await _plant_expired_row(temp_cache_path)

    await cache.put("ep", {"k": "one"}, {"v": 1})
    assert await _row_count(temp_cache_path) == 2, "first write must not sweep yet"

    await cache.put("ep", {"k": "two"}, {"v": 2})
    assert await _row_count(temp_cache_path) == 2, "second write sweeps the stale row"
    assert await cache.get("ep", {"k": "one"}) == {"v": 1}
    assert await cache.get("ep", {"k": "two"}) == {"v": 2}
