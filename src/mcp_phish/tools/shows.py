"""Show-keyed tools: everything you reach by naming a show, a venue, or a date.

``search_shows``, ``get_show``, ``recent_shows``, ``get_reviews``,
``venue_history``. Reviews live here because a review is addressed by the show
it is about, not by any song.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp_phish.clients.phishnet import PhishNetError
from mcp_phish.mappers import phishnet as pn_map
from mcp_phish.mappers import vault as vault_map
from mcp_phish.mappers.coerce import safe_str
from mcp_phish.responses import err, ok
from mcp_phish.runtime import READ_ONLY, ServerContext

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger("mcp_phish.server")

__all__ = ["register"]


def _looks_like_date(value: str) -> bool:
    return len(value) == 10 and value.count("-") == 2


def register(mcp: FastMCP, ctx: ServerContext) -> None:
    """Register the show-domain tools against ``mcp``."""

    @mcp.tool(annotations=READ_ONLY)
    async def search_shows(
        year: int | None = None,
        venue: str = "",
        city: str = "",
        state: str = "",
        country: str = "",
        limit: int = 25,
    ) -> str:
        """Search Phish shows by year + venue + city/state/country.

        All filters are optional and combine with AND semantics on the
        upstream side. ``limit`` caps the local result count after the
        upstream returns; raise it for broader sweeps.

        Args:
            year: Four-digit year (e.g. ``1997``). Optional.
            venue: Substring match against venue name.
            city: Substring match against city.
            state: Two-letter state/province abbreviation.
            country: Country name (e.g. ``"USA"``).
            limit: Max rows to return after filtering. Default 25.

        Returns:
            JSON ``{"data": [ShowSummary, ...]}``. Each ShowSummary has
            ``show_id, date, venue_name, location, tour_name``.

        Idempotent. Example: ``search_shows(year=1997, venue="MSG", limit=5)``.
        """
        params: dict[str, Any] = {}
        if year is not None:
            params["year"] = year
        if venue:
            params["venue"] = venue
        if city:
            params["city"] = city
        if state:
            params["state"] = state
        if country:
            params["country"] = country
        vr = await ctx.vault_reader()
        if vr is not None:
            try:
                rows = await vr.search_shows(
                    year=year,
                    venue=venue,
                    city=city,
                    state=state,
                    country=country,
                    limit=limit,
                )
                return ok([vault_map.show_summary(row) for row in rows])
            except Exception:
                logger.exception("vault search_shows failed; falling back to live")
        try:
            payload = await ctx.cached_phishnet(
                "search_shows", params, lambda: ctx.phishnet.search_shows(params)
            )
            rows_live = payload if isinstance(payload, list) else []
            summaries = [pn_map.show_summary(row) for row in rows_live[:limit]]
            return ok(summaries)
        except PhishNetError as exc:
            logger.exception("search_shows failed")
            return err(str(exc), "UPSTREAM_DOWN", upstream="phish.net")

    @mcp.tool(annotations=READ_ONLY)
    async def get_show(date_or_id: str) -> str:
        """Get a single Phish show with full setlist, ratings, and venue.

        ``date_or_id`` may be a YYYY-MM-DD date or a phish.net showid.

        Args:
            date_or_id: ``"1995-12-30"`` or a numeric showid string.

        Returns:
            JSON ``{"data": Show}``. ``Show`` is the frozen public model
            (date, venue, setlist[], rating, rating_count, review_count,
            setlist_notes).

        Idempotent. Example: ``get_show("1995-12-30")``.
        """
        if not date_or_id:
            return err("date_or_id is required", "INVALID_INPUT")
        is_date = _looks_like_date(date_or_id)
        vr = await ctx.vault_reader()
        use_vault = vr is not None and not (is_date and ctx.is_hot_window(date_or_id))
        if use_vault:
            assert vr is not None
            try:
                show_row, setlist_rows = await vr.get_show(date_or_id)
                if show_row is None:
                    return err(f"show not found: {date_or_id}", "NOT_FOUND")
                return ok(vault_map.show_full(show_row, setlist_rows))
            except Exception:
                logger.exception(
                    "vault get_show failed; falling back to live", extra={"date_or_id": date_or_id}
                )
        hot_ttl = ctx.hot_ttl(date_or_id, is_date=is_date)
        try:
            if is_date:
                show_payload = await ctx.cached_phishnet(
                    "get_show_by_date",
                    {"date": date_or_id},
                    lambda: ctx.phishnet.get_show_by_date(date_or_id),
                    ttl_override=hot_ttl,
                )
                setlist_payload = await ctx.cached_phishnet(
                    "get_setlist_by_date",
                    {"date": date_or_id},
                    lambda: ctx.phishnet.get_setlist_by_date(date_or_id),
                    ttl_override=hot_ttl,
                )
            else:
                show_payload = await ctx.cached_phishnet(
                    "get_show_by_id",
                    {"id": date_or_id},
                    lambda: ctx.phishnet.get_show_by_id(date_or_id),
                )
                # Setlist by id requires a date round-trip; fall back to date.
                show_rows_for_date = (
                    show_payload if isinstance(show_payload, list) else [show_payload]
                )
                date_for_setlist = (
                    show_rows_for_date[0].get("showdate") if show_rows_for_date else ""
                )
                setlist_payload = []
                if date_for_setlist:
                    setlist_payload = await ctx.cached_phishnet(
                        "get_setlist_by_date",
                        {"date": date_for_setlist},
                        lambda: ctx.phishnet.get_setlist_by_date(date_for_setlist),
                        ttl_override=ctx.hot_ttl(date_for_setlist),
                    )
            rows = show_payload if isinstance(show_payload, list) else [show_payload]
            if not rows:
                return err(f"show not found: {date_or_id}", "NOT_FOUND")
            setlist_rows_live = setlist_payload if isinstance(setlist_payload, list) else []
            return ok(pn_map.show_full(rows[0], setlist_rows_live))
        except PhishNetError as exc:
            logger.exception("get_show failed", extra={"date_or_id": date_or_id})
            code = "NOT_FOUND" if "no show" in str(exc).lower() else "UPSTREAM_DOWN"
            return err(str(exc), code, upstream="phish.net")

    @mcp.tool(annotations=READ_ONLY)
    async def recent_shows(limit: int = 10) -> str:
        """List the most recent Phish shows.

        Args:
            limit: Max rows to return. Default 10. Capped at 100.

        Returns:
            JSON ``{"data": [ShowSummary, ...]}``, ordered most-recent-first.

        Idempotent. Example: ``recent_shows(limit=5)``.
        """
        capped = max(1, min(int(limit), 100))
        vr = await ctx.vault_reader()
        if vr is not None:
            try:
                rows = await vr.recent_shows(limit=capped)
                return ok([vault_map.show_summary(row) for row in rows])
            except Exception:
                logger.exception("vault recent_shows failed; falling back to live")
        params: dict[str, Any] = {"order_by": "showdate.desc", "limit": capped}
        # recent_shows always surfaces the newest show, which may be in
        # progress; short-TTL it so a same-night addition isn't frozen for 24h.
        try:
            payload = await ctx.cached_phishnet(
                "recent_shows",
                params,
                lambda: ctx.phishnet.search_shows(params),
                ttl_override=ctx.settings.hot_window_cache_ttl_seconds,
            )
            rows_live = payload if isinstance(payload, list) else []
            # Sort defensively: stub may not respect order_by.
            rows_live = sorted(rows_live, key=lambda r: safe_str(r.get("showdate")), reverse=True)
            summaries = [pn_map.show_summary(row) for row in rows_live[:capped]]
            return ok(summaries)
        except PhishNetError as exc:
            logger.exception("recent_shows failed")
            return err(str(exc), "UPSTREAM_DOWN", upstream="phish.net")

    @mcp.tool(annotations=READ_ONLY)
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
            return err("show_id_or_date is required", "INVALID_INPUT")
        capped = max(1, min(int(limit), 200))
        is_date = _looks_like_date(show_id_or_date)
        vr = await ctx.vault_reader()
        # Vault reviews are indexed by show date only; skip vault for numeric ids.
        if vr is not None and is_date:
            try:
                rows = await vr.get_reviews(show_date=show_id_or_date, limit=capped)
                return ok([vault_map.review(row) for row in rows])
            except Exception:
                logger.exception("vault get_reviews failed; falling back to live")
        # Reviews for a same-night show trickle in live; short-TTL the hot path.
        reviews_hot_ttl = ctx.hot_ttl(show_id_or_date, is_date=is_date)
        try:
            if is_date:
                payload = await ctx.cached_phishnet(
                    "get_reviews_by_date",
                    {"date": show_id_or_date},
                    lambda: ctx.phishnet.reviews_by_date(show_id_or_date),
                    ttl_override=reviews_hot_ttl,
                )
            else:
                payload = await ctx.cached_phishnet(
                    "get_reviews_by_id",
                    {"id": show_id_or_date},
                    lambda: ctx.phishnet.reviews_by_id(show_id_or_date),
                )
            rows_live = payload if isinstance(payload, list) else []
            return ok([pn_map.review(row) for row in rows_live[:capped]])
        except PhishNetError as exc:
            logger.exception("get_reviews failed")
            return err(str(exc), "UPSTREAM_DOWN", upstream="phish.net")

    @mcp.tool(annotations=READ_ONLY)
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
        vr = await ctx.vault_reader()
        if vr is None:
            return err("venue_history requires vault (VAULT_ENABLED=true)", "VAULT_DISABLED")
        if not venue_slug:
            return err("venue_slug is required", "INVALID_INPUT")
        capped = max(1, min(int(limit), 200))
        try:
            rows = await vr.venue_history(venue_slug=venue_slug, limit=capped)
            return ok([vault_map.venue_show(row) for row in rows])
        except Exception as exc:
            logger.exception("venue_history failed", extra={"venue_slug": venue_slug})
            return err(str(exc), "VAULT_ERROR")
