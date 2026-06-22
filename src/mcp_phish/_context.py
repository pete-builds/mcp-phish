"""Shared runtime context for the tool modules.

``build_server`` wires the upstream clients, response cache, throttles, and
vault reader once, bundles them into a :class:`ServerContext`, and hands the
context to each module's ``register(mcp, ctx)`` entrypoint. Tool bodies read
their dependencies off the context instead of closing over locals in a giant
factory function.

The cache-fetch helpers and vault-resolution logic that used to be nested
closures inside ``build_server`` are now methods here, so their behavior is
unchanged but they're reachable from any module.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mcp_phish.config import Settings
from mcp_phish.mappers import _ckey_phishin, _ckey_phishnet, _PhishInLike, _PhishNetLike
from mcp_phish.vault import VaultReader

if TYPE_CHECKING:
    from mcp_phish.cache import ResponseCache
    from mcp_phish.throttle import TokenBucket

logger = logging.getLogger("mcp_phish.server")


class ServerContext:
    """Bundle of the runtime dependencies every tool module needs.

    Constructed once by :func:`mcp_phish.server.build_server`. Holds the
    upstream clients, response cache, per-upstream throttles, settings, and the
    vault wiring (explicit reader, explicit pool, or lazy pool created on first
    vault read). The cache-fetch helpers and vault resolver are methods so the
    behavior is identical to the previous in-factory closures.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        pn: _PhishNetLike,
        pi: _PhishInLike,
        response_cache: ResponseCache,
        pn_throttle: TokenBucket,
        pi_throttle: TokenBucket,
        vault_reader: VaultReader | None,
        started_at: float,
    ) -> None:
        self.settings = settings
        self.pn = pn
        self.pi = pi
        self.response_cache = response_cache
        self.pn_throttle = pn_throttle
        self.pi_throttle = pi_throttle
        self.started_at = started_at
        self._vault_reader = vault_reader
        self._lazy_pool_holder: list[Any] = [None]  # mutable cell for lazy pool

    async def get_vault_reader(self) -> VaultReader | None:
        """Return the VaultReader, lazily initialising the pool when needed."""
        if self._vault_reader is not None:
            return self._vault_reader
        if not self.settings.vault_enabled:
            return None
        # Lazy pool creation on first vault read.
        if self._lazy_pool_holder[0] is None:
            try:
                import asyncpg as _asyncpg

                self._lazy_pool_holder[0] = await _asyncpg.create_pool(
                    self.settings.pg_dsn,
                    min_size=1,
                    max_size=5,
                )
                logger.info("vault pool created", extra={"dsn_host": self.settings.pg_host})
            except Exception:
                logger.exception("failed to create vault pool")
                return None
        return VaultReader(self._lazy_pool_holder[0])

    def is_hot_window(self, date_str: str) -> bool:
        """Return True if show date is within vault_hot_window_hours of now."""
        try:
            show_dt = datetime.fromisoformat(date_str)
            if show_dt.tzinfo is None:
                show_dt = show_dt.replace(tzinfo=UTC)
            age_hours = (datetime.now(tz=UTC) - show_dt).total_seconds() / 3600
            return age_hours < self.settings.vault_hot_window_hours
        except (ValueError, OverflowError):
            return False

    async def cached_phishnet(
        self,
        endpoint: str,
        params: dict[str, Any],
        call: Any,
        *,
        ttl_override: int | None = None,
    ) -> Any:
        await self.response_cache.init()
        cache_key, cache_params = _ckey_phishnet(endpoint, **params)
        hit = await self.response_cache.get(cache_key, cache_params, ttl_override=ttl_override)
        if hit is not None:
            return hit
        payload = await call()
        await self.response_cache.put(cache_key, cache_params, payload)
        return payload

    async def cached_phishin(
        self,
        endpoint: str,
        params: dict[str, Any],
        call: Any,
        *,
        ttl_override: int | None = None,
    ) -> Any:
        await self.response_cache.init()
        cache_key, cache_params = _ckey_phishin(endpoint, **params)
        hit = await self.response_cache.get(cache_key, cache_params, ttl_override=ttl_override)
        if hit is not None:
            return hit
        payload = await call()
        await self.response_cache.put(cache_key, cache_params, payload)
        return payload


__all__ = ["ServerContext"]
