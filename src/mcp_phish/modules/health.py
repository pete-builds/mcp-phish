"""Health module — the ``health`` meta tool (no upstream calls)."""

from __future__ import annotations

import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from mcp_phish import __version__
from mcp_phish._common import _ok
from mcp_phish.models import (
    CacheHealth,
    Health,
    UpstreamHealth,
    VaultHealth,
)
from mcp_phish.modules._audit import audited

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from mcp_phish._context import ServerContext

logger = logging.getLogger("mcp_phish.server")


def register(mcp: FastMCP, ctx: ServerContext) -> None:
    settings = ctx.settings

    @mcp.tool()
    @audited("health")
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

        # Touch the cache so size_bytes is honest after the first call.
        with contextlib.suppress(Exception):  # pragma: no cover — surfaced as "degraded"
            await ctx.response_cache.init()

        pn_snap = ctx.pn_throttle.snapshot()
        pi_snap = ctx.pi_throttle.snapshot()

        # Build vault health snapshot.
        vault_health_status = "ok"
        vault_h: VaultHealth
        vr = await ctx.get_vault_reader()
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
                size_bytes=ctx.response_cache.size_bytes(),
                ttl_seconds=settings.cache_ttl_seconds,
                last_hit_ts=_iso(ctx.response_cache.last_hit_ts),
                last_miss_ts=_iso(ctx.response_cache.last_miss_ts),
            ),
            vault=vault_h,
        )
        # Surface uptime in extras for log-side observability.
        logger.debug("health snapshot", extra={"uptime_s": int(time.time() - ctx.started_at)})
        return _ok(report)


__all__ = ["register"]
