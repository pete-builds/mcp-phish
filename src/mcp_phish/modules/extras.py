"""Extras module — reviews plus vault-only analytics.

Tools: get_reviews (phish.net), venue_history and songs_by_gap (vault-only).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mcp_phish._common import _err, _ok
from mcp_phish.clients.phishnet import PhishNetError
from mcp_phish.mappers import (
    _phishnet_review,
    _vault_review,
    _vault_song_gap,
    _vault_venue_show,
)
from mcp_phish.modules._audit import audited

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from mcp_phish._context import ServerContext

logger = logging.getLogger("mcp_phish.server")


def register(mcp: FastMCP, ctx: ServerContext) -> None:
    settings = ctx.settings

    @mcp.tool()
    @audited("get_reviews")
    async def get_reviews(show_id_or_date: str, limit: int = 25) -> str:
        """Fetch user reviews for a show.

        Args:
            show_id_or_date: YYYY-MM-DD or phish.net showid.
            limit: Max rows. Default 25, capped at 200.

        Returns:
            JSON ``{"data": [Review, ...]}``. Each Review has
            ``review_id, show_id, date, author, posted_at, rating, text``.

        Idempotent. Example: ``get_reviews("1995-12-30", limit=5)``.
        """
        if not show_id_or_date:
            return _err("show_id_or_date is required", "INVALID_INPUT")
        capped = max(1, min(int(limit), 200))
        is_date = len(show_id_or_date) == 10 and show_id_or_date.count("-") == 2
        vr = await ctx.get_vault_reader()
        # Vault reviews are indexed by show date only; skip vault for numeric ids.
        if vr is not None and is_date:
            try:
                rows = await vr.get_reviews(show_date=show_id_or_date, limit=capped)
                return _ok([_vault_review(row) for row in rows])
            except Exception:
                logger.exception("vault get_reviews failed; falling back to live")
        # Reviews for a same-night show trickle in live; short-TTL the hot path.
        reviews_hot_ttl = (
            settings.hot_window_cache_ttl_seconds
            if is_date and ctx.is_hot_window(show_id_or_date)
            else None
        )
        try:
            if is_date:
                payload = await ctx.cached_phishnet(
                    "get_reviews_by_date",
                    {"date": show_id_or_date},
                    lambda: ctx.pn.reviews_by_date(show_id_or_date),
                    ttl_override=reviews_hot_ttl,
                )
            else:
                payload = await ctx.cached_phishnet(
                    "get_reviews_by_id",
                    {"id": show_id_or_date},
                    lambda: ctx.pn.reviews_by_id(show_id_or_date),
                )
            rows_live = payload if isinstance(payload, list) else []
            return _ok([_phishnet_review(row) for row in rows_live[:capped]])
        except PhishNetError as exc:
            logger.exception("get_reviews failed")
            return _err(str(exc), "UPSTREAM_DOWN", upstream="phish.net")

    @mcp.tool()
    @audited("venue_history")
    async def venue_history(venue_slug: str, limit: int = 25) -> str:
        """List all shows at a venue, most recent first. Requires vault.

        Args:
            venue_slug: Phish.net venue slug (e.g. ``"madison-square-garden"``).
            limit: Max rows to return. Default 25, capped at 200.

        Returns:
            JSON ``{"data": [VenueShow, ...]}``. Each VenueShow has
            ``show_id, date, venue_name, location, tour_name``.

        Vault-only. Returns ``VAULT_DISABLED`` error if vault is not enabled.
        Idempotent. Example: ``venue_history("madison-square-garden", limit=10)``.
        """
        vr = await ctx.get_vault_reader()
        if vr is None:
            return _err("venue_history requires vault (VAULT_ENABLED=true)", "VAULT_DISABLED")
        if not venue_slug:
            return _err("venue_slug is required", "INVALID_INPUT")
        capped = max(1, min(int(limit), 200))
        try:
            rows = await vr.venue_history(venue_slug=venue_slug, limit=capped)
            return _ok([_vault_venue_show(row) for row in rows])
        except Exception as exc:
            logger.exception("venue_history failed", extra={"venue_slug": venue_slug})
            return _err(str(exc), "VAULT_ERROR")

    @mcp.tool()
    @audited("songs_by_gap")
    async def songs_by_gap(limit: int = 25) -> str:
        """List songs ordered by current gap (shows since last play), descending.

        "Gap" means the number of shows since the song was last performed.
        High-gap songs are overdue; lower-gap songs were recently played.
        Only songs with a known gap are included.

        Args:
            limit: Max rows to return. Default 25, capped at 200.

        Returns:
            JSON ``{"data": [SongGap, ...]}``. Each SongGap has
            ``slug, title, times_played, gap_current, last_played_date``.

        Vault-only. Returns ``VAULT_DISABLED`` error if vault is not enabled.
        Idempotent. Example: ``songs_by_gap(limit=10)``.
        """
        vr = await ctx.get_vault_reader()
        if vr is None:
            return _err("songs_by_gap requires vault (VAULT_ENABLED=true)", "VAULT_DISABLED")
        capped = max(1, min(int(limit), 200))
        try:
            rows = await vr.songs_by_gap(limit=capped)
            return _ok([_vault_song_gap(row) for row in rows])
        except Exception as exc:
            logger.exception("songs_by_gap failed")
            return _err(str(exc), "VAULT_ERROR")


__all__ = ["register"]
