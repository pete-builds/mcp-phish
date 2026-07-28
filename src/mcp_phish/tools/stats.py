"""Aggregate tools: corpus-wide roll-ups rather than record lookups.

``stats_overview``. Everything in ``shows``/``songs``/``audio`` answers "tell
me about this one thing"; this module answers "tell me about the corpus". The
aggregation is done in SQL by the vault and projected by
``mappers.vault.stats_overview``, so the tool body stays a thin guard.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mcp_phish.mappers import vault as vault_map
from mcp_phish.responses import err, ok
from mcp_phish.runtime import ServerContext

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger("mcp_phish.server")

__all__ = ["register"]


def register(mcp: FastMCP, ctx: ServerContext) -> None:
    """Register the aggregate-stats tools against ``mcp``."""

    @mcp.tool()
    async def stats_overview(top_n: int = 10) -> str:
        """Catalog-wide Phish statistics in one read-only roll-up.

        Aggregates the whole setlist corpus: total shows, total songs tracked,
        distinct songs ever played, average songs per show, plus ranked slices
        for most-played songs, biggest current gaps (bust-out candidates),
        rarest songs, recent debuts, and the longest shows by song count.

        Args:
            top_n: Length of each ranked list (most-played, gaps, rarest,
                debuts, longest shows). Default 10, capped at 50.

        Returns:
            JSON ``{"data": StatsOverview}``. StatsOverview has
            ``total_shows, total_songs_tracked, distinct_songs_played,
            total_performances, avg_songs_per_show, first_show_date,
            last_show_date, most_played[], biggest_gaps[], rarest_songs[],
            recent_debuts[], longest_shows[]``.

        Vault-only. Returns ``VAULT_DISABLED`` error if vault is not enabled.
        Idempotent. Example: ``stats_overview(top_n=10)``.
        """
        vr = await ctx.vault_reader()
        if vr is None:
            return err("stats_overview requires vault (VAULT_ENABLED=true)", "VAULT_DISABLED")
        try:
            raw = await vr.stats_overview(top_n=top_n)
            return ok(vault_map.stats_overview(raw))
        except Exception as exc:
            logger.exception("stats_overview failed")
            return err(str(exc), "VAULT_ERROR")
