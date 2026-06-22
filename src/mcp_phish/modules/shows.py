"""Shows module — phish.net show queries: search_shows, get_show, recent_shows."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp_phish._common import _err, _ok
from mcp_phish.clients.phishnet import PhishNetError
from mcp_phish.mappers import (
    _phishnet_show_full,
    _phishnet_show_summary,
    _safe_str,
    _vault_show_full,
    _vault_show_summary,
)
from mcp_phish.modules._audit import audited

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from mcp_phish._context import ServerContext

logger = logging.getLogger("mcp_phish.server")


def register(mcp: FastMCP, ctx: ServerContext) -> None:
    settings = ctx.settings

    @mcp.tool()
    @audited("search_shows")
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
        vr = await ctx.get_vault_reader()
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
                return _ok([_vault_show_summary(row) for row in rows])
            except Exception:
                logger.exception("vault search_shows failed; falling back to live")
        try:
            payload = await ctx.cached_phishnet(
                "search_shows", params, lambda: ctx.pn.search_shows(params)
            )
            rows_live = payload if isinstance(payload, list) else []
            summaries = [_phishnet_show_summary(row) for row in rows_live[:limit]]
            return _ok(summaries)
        except PhishNetError as exc:
            logger.exception("search_shows failed")
            return _err(str(exc), "UPSTREAM_DOWN", upstream="phish.net")

    @mcp.tool()
    @audited("get_show")
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
            return _err("date_or_id is required", "INVALID_INPUT")
        is_date = len(date_or_id) == 10 and date_or_id.count("-") == 2
        vr = await ctx.get_vault_reader()
        use_vault = vr is not None and not (is_date and ctx.is_hot_window(date_or_id))
        if use_vault:
            assert vr is not None
            try:
                show_row, setlist_rows = await vr.get_show(date_or_id)
                if show_row is None:
                    return _err(f"show not found: {date_or_id}", "NOT_FOUND")
                return _ok(_vault_show_full(show_row, setlist_rows))
            except Exception:
                logger.exception(
                    "vault get_show failed; falling back to live", extra={"date_or_id": date_or_id}
                )
        # A live read of a show inside the hot window (setlist still being
        # typed in on phish.net) gets a short cache TTL so frequent polls see
        # updates within ~90s instead of a frozen 24h snapshot. Historical
        # reads keep the default TTL.
        hot_ttl = (
            settings.hot_window_cache_ttl_seconds
            if is_date and ctx.is_hot_window(date_or_id)
            else None
        )
        try:
            if is_date:
                show_payload = await ctx.cached_phishnet(
                    "get_show_by_date",
                    {"date": date_or_id},
                    lambda: ctx.pn.get_show_by_date(date_or_id),
                    ttl_override=hot_ttl,
                )
                setlist_payload = await ctx.cached_phishnet(
                    "get_setlist_by_date",
                    {"date": date_or_id},
                    lambda: ctx.pn.get_setlist_by_date(date_or_id),
                    ttl_override=hot_ttl,
                )
            else:
                show_payload = await ctx.cached_phishnet(
                    "get_show_by_id",
                    {"id": date_or_id},
                    lambda: ctx.pn.get_show_by_id(date_or_id),
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
                    setlist_hot_ttl = (
                        settings.hot_window_cache_ttl_seconds
                        if ctx.is_hot_window(date_for_setlist)
                        else None
                    )
                    setlist_payload = await ctx.cached_phishnet(
                        "get_setlist_by_date",
                        {"date": date_for_setlist},
                        lambda: ctx.pn.get_setlist_by_date(date_for_setlist),
                        ttl_override=setlist_hot_ttl,
                    )
            rows = show_payload if isinstance(show_payload, list) else [show_payload]
            if not rows:
                return _err(f"show not found: {date_or_id}", "NOT_FOUND")
            setlist_rows_live = setlist_payload if isinstance(setlist_payload, list) else []
            return _ok(_phishnet_show_full(rows[0], setlist_rows_live))
        except PhishNetError as exc:
            logger.exception("get_show failed", extra={"date_or_id": date_or_id})
            code = "NOT_FOUND" if "no show" in str(exc).lower() else "UPSTREAM_DOWN"
            return _err(str(exc), code, upstream="phish.net")

    @mcp.tool()
    @audited("recent_shows")
    async def recent_shows(limit: int = 10) -> str:
        """List the most recent Phish shows.

        Args:
            limit: Max rows to return. Default 10. Capped at 100.

        Returns:
            JSON ``{"data": [ShowSummary, ...]}``, ordered most-recent-first.

        Idempotent. Example: ``recent_shows(limit=5)``.
        """
        capped = max(1, min(int(limit), 100))
        vr = await ctx.get_vault_reader()
        if vr is not None:
            try:
                rows = await vr.recent_shows(limit=capped)
                return _ok([_vault_show_summary(row) for row in rows])
            except Exception:
                logger.exception("vault recent_shows failed; falling back to live")
        params: dict[str, Any] = {"order_by": "showdate.desc", "limit": capped}
        # recent_shows always surfaces the newest show, which may be in
        # progress; short-TTL it so a same-night addition isn't frozen for 24h.
        try:
            payload = await ctx.cached_phishnet(
                "recent_shows",
                params,
                lambda: ctx.pn.search_shows(params),
                ttl_override=settings.hot_window_cache_ttl_seconds,
            )
            rows_live = payload if isinstance(payload, list) else []
            # Sort defensively: stub may not respect order_by.
            rows_live = sorted(rows_live, key=lambda r: _safe_str(r.get("showdate")), reverse=True)
            summaries = [_phishnet_show_summary(row) for row in rows_live[:capped]]
            return _ok(summaries)
        except PhishNetError as exc:
            logger.exception("recent_shows failed")
            return _err(str(exc), "UPSTREAM_DOWN", upstream="phish.net")


__all__ = ["register"]
