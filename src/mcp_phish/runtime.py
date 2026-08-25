"""Runtime state: the wiring every tool shares, plus the tool that reports on it.

A :class:`ServerContext` is the single object handed to each tool module at
registration time. It owns the things that exist for the life of the process
and belong to no data domain: the two upstream clients, the response cache,
the two token buckets, the vault reader (eagerly injected or lazily created),
the hot-window rule, and the process start time.

``health`` lives here rather than under ``tools/`` because it is a readout of
exactly this state. It touches no upstream and projects no domain row; it
reports throttle tokens, cache size, and vault staleness. Filing it under a
data domain would be a lie about what it reads.
"""

from __future__ import annotations

import contextlib
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from mcp_phish import __version__
from mcp_phish.cache import ResponseCache
from mcp_phish.clients.stubs import StubPhishInClient, StubPhishNetClient
from mcp_phish.config import Settings
from mcp_phish.hotwindow import is_hot as hot_is_hot
from mcp_phish.models import CacheHealth, Health, UpstreamHealth, VaultHealth
from mcp_phish.responses import ok
from mcp_phish.throttle import TokenBucket
from mcp_phish.vault import VaultReader

if TYPE_CHECKING:
    import asyncpg
    from fastmcp import FastMCP

logger = logging.getLogger("mcp_phish.server")

# --- Tool annotations ---
# Seventeen tools, every one of them a lookup, and not one writes anything
# anywhere. That is worth DECLARING rather than leaving to be inferred: an
# unannotated read-only server and an unannotated server full of delete tools
# are indistinguishable in the manifest, so a client trying to be careful has
# to be careful about everything, which in practice means being careful about
# nothing. Saying "these seventeen are safe" is what makes "that one is not",
# elsewhere in the fleet, mean something.
#
# openWorldHint is True throughout, including `health`. Every read reaches
# either the phish.net API or the vault, which is a Postgres server on another
# host, so an answer can differ between two identical calls because the world
# moved -- not because the call changed it. That is why these are open-world
# and idempotent at the same time.

#: Reads only. Safe to repeat, safe to call speculatively.
READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

__all__ = [
    "PhishInLike",
    "PhishNetLike",
    "ServerContext",
    "build_context",
    "register",
]


# ---------------------------------------------------------------------------
# Client protocols (so stubs and real clients are duck-type compatible)
# ---------------------------------------------------------------------------


class PhishNetLike(Protocol):
    async def get_show_by_date(self, date: str) -> Any: ...
    async def get_show_by_id(self, show_id: str) -> Any: ...
    async def search_shows(self, params: dict[str, Any]) -> Any: ...
    async def get_setlist_by_date(self, date: str) -> Any: ...
    async def list_songs(self, params: dict[str, Any] | None = None) -> Any: ...
    async def get_song_by_slug(self, slug: str) -> Any: ...
    async def search_songs(self, params: dict[str, Any]) -> Any: ...
    async def song_performances(self, slug: str, params: dict[str, Any] | None = None) -> Any: ...
    async def jam_chart(self, params: dict[str, Any] | None = None) -> Any: ...
    async def reviews_by_date(self, date: str) -> Any: ...
    async def reviews_by_id(self, show_id: str) -> Any: ...
    async def aclose(self) -> None: ...


class PhishInLike(Protocol):
    async def get_show(self, date_or_id: str) -> Any: ...
    async def get_track(self, track_id: int) -> Any: ...
    async def search_tracks(self, params: dict[str, Any]) -> Any: ...
    async def aclose(self) -> None: ...


# ---------------------------------------------------------------------------
# Server context
# ---------------------------------------------------------------------------


@dataclass
class ServerContext:
    """Everything a tool needs that outlives a single tool call."""

    settings: Settings
    phishnet: PhishNetLike
    phishin: PhishInLike
    cache: ResponseCache
    phishnet_throttle: TokenBucket
    phishin_throttle: TokenBucket
    started_at: float = field(default_factory=time.time)
    #: Eagerly-supplied reader. When ``None`` and the vault is enabled, a
    #: reader is created on the first read from a lazily-built pool.
    vault_reader_override: VaultReader | None = None
    _lazy_pool: list[Any] = field(default_factory=lambda: [None])

    async def vault_reader(self) -> VaultReader | None:
        """Return the VaultReader, lazily initialising the pool when needed."""
        if self.vault_reader_override is not None:
            return self.vault_reader_override
        if not self.settings.vault_enabled:
            return None
        # Lazy pool creation on first vault read.
        if self._lazy_pool[0] is None:
            try:
                import asyncpg as _asyncpg

                self._lazy_pool[0] = await _asyncpg.create_pool(
                    self.settings.pg_dsn,
                    min_size=1,
                    max_size=5,
                )
                logger.info("vault pool created", extra={"dsn_host": self.settings.pg_host})
            except Exception:
                logger.exception("failed to create vault pool")
                return None
        return VaultReader(self._lazy_pool[0])

    def is_hot_window(self, date_str: str) -> bool:
        """Return True if the show should be read live (see mcp_phish.hotwindow)."""
        return hot_is_hot(date_str, self.settings.vault_hot_window_hours, datetime.now(tz=UTC))

    def hot_ttl(self, date_str: str, *, is_date: bool = True) -> int | None:
        """Short cache TTL for a show still inside its hot window, else ``None``.

        A live read of an in-progress show (setlist still being typed in on
        phish.net, audio still uploading on phish.in) gets a short TTL so
        frequent polls see updates in ~90s instead of a frozen 24h snapshot.
        Historical reads keep the default TTL.
        """
        if is_date and self.is_hot_window(date_str):
            return self.settings.hot_window_cache_ttl_seconds
        return None

    async def cached_phishnet(
        self,
        endpoint: str,
        params: dict[str, Any],
        call: Any,
        *,
        ttl_override: int | None = None,
    ) -> Any:
        return await self._cached("phishnet", endpoint, params, call, ttl_override=ttl_override)

    async def cached_phishin(
        self,
        endpoint: str,
        params: dict[str, Any],
        call: Any,
        *,
        ttl_override: int | None = None,
    ) -> Any:
        return await self._cached("phishin", endpoint, params, call, ttl_override=ttl_override)

    async def _cached(
        self,
        source: str,
        endpoint: str,
        params: dict[str, Any],
        call: Any,
        *,
        ttl_override: int | None = None,
    ) -> Any:
        await self.cache.init()
        cache_key = f"{source}:{endpoint}"
        hit = await self.cache.get(cache_key, params, ttl_override=ttl_override)
        if hit is not None:
            return hit
        payload = await call()
        await self.cache.put(cache_key, params, payload)
        return payload


def build_context(
    settings: Settings,
    *,
    phishnet_client: PhishNetLike | None = None,
    phishin_client: PhishInLike | None = None,
    cache: ResponseCache | None = None,
    phishnet_throttle: TokenBucket | None = None,
    phishin_throttle: TokenBucket | None = None,
    vault_reader: VaultReader | None = None,
    vault_pool: asyncpg.Pool | None = None,
) -> ServerContext:
    """Resolve stubs vs real clients and assemble the shared runtime state.

    ``vault_reader`` takes precedence over ``vault_pool`` when both are given.
    When ``vault_pool`` is given, a ``VaultReader`` is constructed from it.
    When neither is given and ``settings.vault_enabled`` is True, the pool is
    created lazily on the first vault read.
    """
    pn_throttle = phishnet_throttle or TokenBucket(rps=settings.throttle_phishnet_rps)
    pi_throttle = phishin_throttle or TokenBucket(rps=settings.throttle_phishin_rps)

    pn: PhishNetLike
    pi: PhishInLike
    if phishnet_client is not None:
        pn = phishnet_client
    elif settings.stub_mode:
        pn = StubPhishNetClient()
    else:
        # Lazy import keeps module-level surface clean for tests.
        from mcp_phish.clients.phishnet import PhishNetClient

        pn = PhishNetClient(
            api_key=settings.phishnet_api_key,
            throttle=pn_throttle,
            base_url=settings.phishnet_base_url,
        )
    if phishin_client is not None:
        pi = phishin_client
    elif settings.stub_mode:
        pi = StubPhishInClient()
    else:
        from mcp_phish.clients.phishin import PhishInClient

        pi = PhishInClient(
            api_key=settings.phishin_api_key,
            throttle=pi_throttle,
            base_url=settings.phishin_base_url,
        )

    response_cache = cache or ResponseCache(
        db_path=settings.cache_db_path,
        ttl_seconds=settings.cache_ttl_seconds,
    )

    # Priority: explicit vault_reader > vault_pool > lazy-init on first use.
    reader: VaultReader | None
    if vault_reader is not None:
        reader = vault_reader
    elif vault_pool is not None:
        reader = VaultReader(vault_pool)
    else:
        reader = None

    return ServerContext(
        settings=settings,
        phishnet=pn,
        phishin=pi,
        cache=response_cache,
        phishnet_throttle=pn_throttle,
        phishin_throttle=pi_throttle,
        vault_reader_override=reader,
    )


# ---------------------------------------------------------------------------
# health — the readout of the state above
# ---------------------------------------------------------------------------


def register(mcp: FastMCP, ctx: ServerContext) -> None:
    """Register the ``health`` tool against ``mcp``."""

    @mcp.tool(annotations=READ_ONLY)
    async def health() -> str:
        """Report server status: stub mode, upstream throttle state, cache stats.

        Calling this tool never touches an upstream. It only reads in-process
        state and the local cache file.

        Returns:
            JSON ``{"data": Health}``. Health.phishnet/phishin contain
            ``reachable, rps_limit, tokens_available, last_call_ts``. Health
            also surfaces the cache path, size in bytes, TTL, and last
            hit/miss timestamps.

        Idempotent. Example: ``health()``.
        """

        def _iso(ts: float | None) -> str | None:
            if ts is None:
                return None
            return datetime.fromtimestamp(ts, tz=UTC).isoformat()

        settings = ctx.settings

        # Touch the cache so size_bytes is honest after the first call.
        with contextlib.suppress(Exception):  # pragma: no cover — surfaced as "degraded"
            await ctx.cache.init()

        pn_snap = ctx.phishnet_throttle.snapshot()
        pi_snap = ctx.phishin_throttle.snapshot()

        # Build vault health snapshot.
        vault_health_status = "ok"
        vault_h: VaultHealth
        vr = await ctx.vault_reader()
        if settings.vault_enabled:
            last_etl_iso: str | None = None
            staleness_hours: float | None = None
            is_stale = False
            if vr is not None:
                with contextlib.suppress(Exception):
                    etl_row = await vr.last_etl_run()
                    if etl_row is not None:
                        finished = etl_row.get("finished_at")
                        if finished is not None:
                            if isinstance(finished, datetime):
                                last_etl_iso = finished.isoformat()
                                staleness_hours = (
                                    datetime.now(tz=UTC) - finished
                                ).total_seconds() / 3600
                            else:
                                last_etl_iso = str(finished)
                            if staleness_hours is not None and (
                                staleness_hours > settings.vault_max_stale_hours
                            ):
                                is_stale = True
                                vault_health_status = "degraded"
            vault_h = VaultHealth(
                enabled=True,
                last_etl_run=last_etl_iso,
                staleness_hours=staleness_hours,
                stale=is_stale,
            )
        else:
            vault_h = VaultHealth(enabled=False)

        report = Health(
            status=vault_health_status,
            stub_mode=settings.stub_mode,
            version=__version__,
            phishnet=UpstreamHealth(
                reachable=True,
                rps_limit=pn_snap.rps,
                tokens_available=pn_snap.tokens_available,
                last_call_ts=_iso(pn_snap.last_call_ts),
            ),
            phishin=UpstreamHealth(
                reachable=True,
                rps_limit=pi_snap.rps,
                tokens_available=pi_snap.tokens_available,
                last_call_ts=_iso(pi_snap.last_call_ts),
            ),
            cache=CacheHealth(
                path=settings.cache_db_path,
                size_bytes=ctx.cache.size_bytes(),
                ttl_seconds=settings.cache_ttl_seconds,
                last_hit_ts=_iso(ctx.cache.last_hit_ts),
                last_miss_ts=_iso(ctx.cache.last_miss_ts),
            ),
            vault=vault_h,
        )
        # Surface uptime in extras for log-side observability.
        logger.debug("health snapshot", extra={"uptime_s": int(time.time() - ctx.started_at)})
        return ok(report)
