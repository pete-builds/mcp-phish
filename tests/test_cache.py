"""Tests for the opaque KV cache."""

from __future__ import annotations

import asyncio

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
