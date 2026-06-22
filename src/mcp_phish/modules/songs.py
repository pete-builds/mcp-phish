"""Songs module — phish.net song catalog + history.

Tools: search_songs, get_song, validate_song_slugs, song_history, jam_chart.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp_phish._common import _err, _ok
from mcp_phish.clients.phishnet import PhishNetError
from mcp_phish.mappers import (
    _phishnet_jam,
    _phishnet_performance,
    _phishnet_song_full,
    _phishnet_song_summary,
    _safe_str,
    _vault_jam,
    _vault_performance,
    _vault_song_full,
    _vault_song_summary,
)
from mcp_phish.models import SlugValidation
from mcp_phish.modules._audit import audited

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from mcp_phish._context import ServerContext

logger = logging.getLogger("mcp_phish.server")


def register(mcp: FastMCP, ctx: ServerContext) -> None:
    @mcp.tool()
    @audited("search_songs")
    async def search_songs(query: str, limit: int = 25) -> str:
        """Search the Phish song catalog by title fragment.

        Args:
            query: Substring matched against song title (case-insensitive
                upstream).
            limit: Max rows to return. Default 25.

        Returns:
            JSON ``{"data": [SongSummary, ...]}``. Each SongSummary has
            ``slug, title, artist, original, times_played``.

        Idempotent. Example: ``search_songs("fluff", limit=5)``.
        """
        if not query:
            return _err("query is required", "INVALID_INPUT")
        vr = await ctx.get_vault_reader()
        if vr is not None:
            try:
                rows = await vr.search_songs(query=query, limit=limit)
                return _ok([_vault_song_summary(row) for row in rows])
            except Exception:
                logger.exception("vault search_songs failed; falling back to live")
        params = {"query": query}
        try:
            payload = await ctx.cached_phishnet(
                "search_songs", params, lambda: ctx.pn.search_songs(params)
            )
            rows_live = payload if isinstance(payload, list) else []
            return _ok([_phishnet_song_summary(row) for row in rows_live[:limit]])
        except PhishNetError as exc:
            logger.exception("search_songs failed")
            return _err(str(exc), "UPSTREAM_DOWN", upstream="phish.net")

    @mcp.tool()
    @audited("get_song")
    async def get_song(slug: str) -> str:
        """Get a single song's catalog record (debut, last play, gap, total).

        Args:
            slug: phish.net song slug (e.g. ``"fluffhead"``, ``"mikes-song"``).

        Returns:
            JSON ``{"data": Song}``. Song fields: slug, title, artist,
            original, times_played, debut_date, last_played_date, gap.

        Idempotent. Example: ``get_song("fluffhead")``.
        """
        if not slug:
            return _err("slug is required", "INVALID_INPUT")
        vr = await ctx.get_vault_reader()
        if vr is not None:
            try:
                row = await vr.get_song(slug)
                if row is None:
                    return _err(f"song not found: {slug}", "NOT_FOUND")
                return _ok(_vault_song_full(row))
            except Exception:
                logger.exception(
                    "vault get_song failed; falling back to live", extra={"slug": slug}
                )
        try:
            payload = await ctx.cached_phishnet(
                "get_song", {"slug": slug}, lambda: ctx.pn.get_song_by_slug(slug)
            )
            rows = payload if isinstance(payload, list) else [payload]
            if not rows:
                return _err(f"song not found: {slug}", "NOT_FOUND")
            return _ok(_phishnet_song_full(rows[0]))
        except PhishNetError as exc:
            logger.exception("get_song failed", extra={"slug": slug})
            code = "NOT_FOUND" if "no song" in str(exc).lower() else "UPSTREAM_DOWN"
            return _err(str(exc), code, upstream="phish.net")

    @mcp.tool()
    @audited("validate_song_slugs")
    async def validate_song_slugs(slugs: list[str]) -> str:
        """Partition a list of song slugs into ``valid`` and ``unknown``.

        Useful for form validation in a downstream client (e.g. phish-game's
        date-pick screen). One round-trip when vault is enabled — the
        live-API fallback fans out to one ``get_song`` call per slug.

        Args:
            slugs: 1 to 50 candidate slugs (e.g. ``["tweezer","fluffhead"]``).
                Empty or oversized lists return ``INVALID_INPUT``.

        Returns:
            JSON ``{"data": {"valid": [...], "unknown": [...]}}``.
            ``valid`` lists the slugs that resolved, in the order the
            vault returned them (sorted by slug for determinism).
            ``unknown`` lists the slugs that did not resolve, preserving
            their request order.

        Idempotent. Read-only. Example:
        ``validate_song_slugs(["tweezer","blarghhh","fluffhead"])`` →
        ``{"valid": ["fluffhead","tweezer"], "unknown": ["blarghhh"]}``.
        """
        if not isinstance(slugs, list) or len(slugs) == 0:
            return _err("slugs must be a non-empty list", "INVALID_INPUT")
        if len(slugs) > 50:
            return _err(
                f"too many slugs ({len(slugs)}); cap is 50",
                "INVALID_INPUT",
                count=len(slugs),
            )
        # Normalise but preserve request order for the unknown list.
        requested: list[str] = [str(s).strip() for s in slugs]
        # Reject empty entries — they are never valid slugs.
        if any(not s for s in requested):
            return _err("slugs must not contain empty strings", "INVALID_INPUT")

        vr = await ctx.get_vault_reader()
        if vr is not None:
            try:
                found_set = await vr.validate_slugs(requested)
                valid_sorted = sorted(found_set)
                unknown = [s for s in requested if s not in found_set]
                return _ok(SlugValidation(valid=valid_sorted, unknown=unknown))
            except Exception as exc:
                logger.exception("vault validate_song_slugs failed; falling back to live")
                # Fall through to live path so a transient pool error isn't fatal.
                _ = exc

        # Live-API fallback: one get_song per slug. PhishNetError -> unknown.
        valid_live: list[str] = []
        unknown_live: list[str] = []
        for slug in requested:
            try:
                payload = await ctx.cached_phishnet(
                    "get_song", {"slug": slug}, lambda s=slug: ctx.pn.get_song_by_slug(s)
                )
                rows = payload if isinstance(payload, list) else [payload]
                if rows and rows[0]:
                    valid_live.append(slug)
                else:
                    unknown_live.append(slug)
            except PhishNetError:
                unknown_live.append(slug)
            except Exception:
                logger.exception("validate_song_slugs upstream error", extra={"slug": slug})
                return _err(
                    "upstream lookup failed during batch validation",
                    "UPSTREAM_DOWN",
                    upstream="phish.net",
                    slug=slug,
                )
        return _ok(SlugValidation(valid=sorted(valid_live), unknown=unknown_live))

    @mcp.tool()
    @audited("song_history")
    async def song_history(slug: str, limit: int = 50) -> str:
        """List every performance of a song, most-recent first.

        Args:
            slug: phish.net song slug.
            limit: Max rows. Default 50, capped at 500.

        Returns:
            JSON ``{"data": [Performance, ...]}``. Each Performance has
            ``show_id, date, venue_name, location, set_name, transition, gap``.

        Idempotent. Example: ``song_history("ghost", limit=20)``.
        """
        if not slug:
            return _err("slug is required", "INVALID_INPUT")
        capped = max(1, min(int(limit), 500))
        vr = await ctx.get_vault_reader()
        if vr is not None:
            try:
                rows = await vr.song_history(slug=slug, limit=capped)
                return _ok([_vault_performance(row) for row in rows])
            except Exception:
                logger.exception(
                    "vault song_history failed; falling back to live", extra={"slug": slug}
                )
        try:
            payload = await ctx.cached_phishnet(
                "song_history",
                {"slug": slug},
                lambda: ctx.pn.song_performances(slug),
            )
            rows_live = payload if isinstance(payload, list) else []
            rows_sorted = sorted(
                rows_live, key=lambda r: _safe_str(r.get("showdate")), reverse=True
            )
            return _ok([_phishnet_performance(row) for row in rows_sorted[:capped]])
        except PhishNetError as exc:
            logger.exception("song_history failed", extra={"slug": slug})
            return _err(str(exc), "UPSTREAM_DOWN", upstream="phish.net")

    @mcp.tool()
    @audited("jam_chart")
    async def jam_chart(year: int | None = None, limit: int = 50) -> str:
        """Return phish.net's jam-chart entries — editorially flagged notable jams.

        Args:
            year: Optional four-digit year filter.
            limit: Max rows. Default 50, capped at 500.

        Returns:
            JSON ``{"data": [NotableJam, ...]}``. Each NotableJam has
            ``show_id, date, song_slug, song_title, venue_name, notes``.

        Idempotent. Example: ``jam_chart(year=1997, limit=10)``.
        """
        capped = max(1, min(int(limit), 500))
        vr = await ctx.get_vault_reader()
        if vr is not None:
            try:
                rows = await vr.jam_chart(year=year, limit=capped)
                return _ok([_vault_jam(row) for row in rows])
            except Exception:
                logger.exception("vault jam_chart failed; falling back to live")
        params: dict[str, Any] = {}
        if year is not None:
            params["year"] = year
        try:
            payload = await ctx.cached_phishnet(
                "jam_chart", params, lambda: ctx.pn.jam_chart(params or None)
            )
            rows_live = payload if isinstance(payload, list) else []
            return _ok([_phishnet_jam(row) for row in rows_live[:capped]])
        except PhishNetError as exc:
            logger.exception("jam_chart failed")
            return _err(str(exc), "UPSTREAM_DOWN", upstream="phish.net")


__all__ = ["register"]
