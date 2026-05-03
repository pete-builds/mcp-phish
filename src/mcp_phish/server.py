"""MCP Phish — wraps api.phish.net v5 + phish.in v2 behind a typed tool surface.

Twelve tools across three domains:

* phish.net  — search_shows, get_show, recent_shows, search_songs, get_song,
               song_history, jam_chart, get_reviews
* phish.in   — get_audio, get_track, search_audio_tracks
* meta       — health

Returns are projected through the public Pydantic models in ``models.py`` so
the wire format stays identical across stub mode, live mode, and the future
Phase 3 vault swap.

Transport: Streamable HTTP via FastMCP (current MCP spec).
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from fastmcp import FastMCP
from pydantic import BaseModel

from mcp_phish import __version__
from mcp_phish.cache import ResponseCache
from mcp_phish.clients.phishin import PhishInError
from mcp_phish.clients.phishnet import PhishNetError
from mcp_phish.clients.stubs import StubPhishInClient, StubPhishNetClient
from mcp_phish.config import Settings, load_settings
from mcp_phish.logging_setup import configure_logging
from mcp_phish.models import (
    CacheHealth,
    Health,
    NotableJam,
    Performance,
    Review,
    SetlistEntry,
    Show,
    ShowAudio,
    ShowSummary,
    Song,
    SongSummary,
    Track,
    UpstreamHealth,
    Venue,
)
from mcp_phish.throttle import TokenBucket

logger = logging.getLogger("mcp_phish.server")


# ---------------------------------------------------------------------------
# Client protocols (so stubs and real clients are duck-type compatible)
# ---------------------------------------------------------------------------


class _PhishNetLike(Protocol):
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


class _PhishInLike(Protocol):
    async def get_show(self, date_or_id: str) -> Any: ...
    async def get_track(self, track_id: int) -> Any: ...
    async def search_tracks(self, params: dict[str, Any]) -> Any: ...
    async def aclose(self) -> None: ...


# ---------------------------------------------------------------------------
# Response envelope helpers (Standard Error Contract)
# ---------------------------------------------------------------------------


def _ok(data: Any) -> str:
    """Serialize a ``data`` payload. Pydantic models flatten via ``model_dump``."""
    return json.dumps({"data": _to_jsonable(data)}, indent=2, default=str)


def _err(message: str, code: str, **details: Any) -> str:
    """Serialize the standard failure shape."""
    payload: dict[str, Any] = {"error": message, "code": code}
    if details:
        payload["details"] = details
    return json.dumps(payload, indent=2, default=str)


def _to_jsonable(obj: Any) -> Any:
    """Recursively convert pydantic models / sequences into JSON-friendly types."""
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_to_jsonable(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# Projection helpers (upstream JSON → Pydantic public models)
#
# Both real and stub clients return the same upstream shape, so a single
# projection layer covers both modes.
# ---------------------------------------------------------------------------


def _safe_str(value: Any) -> str:
    return "" if value is None else str(value)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _phishnet_location(row: dict[str, Any]) -> str:
    city = _safe_str(row.get("city"))
    state = _safe_str(row.get("state"))
    if city and state:
        return f"{city}, {state}"
    return city or state


def _phishnet_show_summary(row: dict[str, Any]) -> ShowSummary:
    return ShowSummary(
        show_id=_safe_str(row.get("showid")),
        date=_safe_str(row.get("showdate")),
        venue_name=_safe_str(row.get("venue")),
        location=_phishnet_location(row),
        tour_name=_safe_str(row.get("tour_name")),
    )


def _phishnet_show_full(show_row: dict[str, Any], setlist_rows: list[dict[str, Any]]) -> Show:
    set_label_map = {"1": "Set 1", "2": "Set 2", "3": "Set 3", "e": "Encore"}
    setlist = [
        SetlistEntry(
            position=_safe_int(row.get("position")),
            set_name=set_label_map.get(_safe_str(row.get("set")), _safe_str(row.get("set"))),
            song_slug=_safe_str(row.get("slug")),
            song_title=_safe_str(row.get("song")),
            transition=_safe_str(row.get("trans_mark")).strip(),
            footnote=_safe_str(row.get("footnote")),
        )
        for row in setlist_rows
    ]
    venue = Venue(
        slug="",
        name=_safe_str(show_row.get("venue")),
        city=_safe_str(show_row.get("city")),
        state=_safe_str(show_row.get("state")),
        country=_safe_str(show_row.get("country")),
        location=_phishnet_location(show_row),
    )
    return Show(
        show_id=_safe_str(show_row.get("showid")),
        date=_safe_str(show_row.get("showdate")),
        venue=venue,
        tour_name=_safe_str(show_row.get("tour_name")),
        setlist=setlist,
        rating=_safe_float(show_row.get("rating")),
        rating_count=_safe_int(show_row.get("rating_count")),
        review_count=_safe_int(show_row.get("review_count")),
        setlist_notes=_safe_str(show_row.get("setlistnotes")),
    )


def _phishnet_song_summary(row: dict[str, Any]) -> SongSummary:
    is_original = bool(row.get("isoriginal", row.get("original", True)))
    return SongSummary(
        slug=_safe_str(row.get("slug")),
        title=_safe_str(row.get("title")),
        artist=row.get("artist") if row.get("artist") else None,
        original=is_original,
        times_played=_safe_int(row.get("times_played")),
    )


def _phishnet_song_full(row: dict[str, Any]) -> Song:
    is_original = bool(row.get("isoriginal", row.get("original", True)))
    debut = row.get("debut") or row.get("debut_date")
    last_played = row.get("last_played") or row.get("last_played_date")
    gap = row.get("gap")
    return Song(
        slug=_safe_str(row.get("slug")),
        title=_safe_str(row.get("title")),
        artist=row.get("artist") if row.get("artist") else None,
        original=is_original,
        times_played=_safe_int(row.get("times_played")),
        debut_date=_safe_str(debut) if debut else None,
        last_played_date=_safe_str(last_played) if last_played else None,
        gap=_safe_int(gap) if gap is not None else None,
    )


def _phishnet_performance(row: dict[str, Any]) -> Performance:
    return Performance(
        show_id=_safe_str(row.get("showid")),
        date=_safe_str(row.get("showdate")),
        venue_name=_safe_str(row.get("venue")),
        location=_phishnet_location(row),
        set_name=_safe_str(row.get("set")),
        transition=_safe_str(row.get("trans_mark")).strip(),
        gap=_safe_int(row.get("gap")) if row.get("gap") is not None else None,
    )


def _phishnet_jam(row: dict[str, Any]) -> NotableJam:
    return NotableJam(
        show_id=_safe_str(row.get("showid")),
        date=_safe_str(row.get("showdate")),
        song_slug=_safe_str(row.get("slug")),
        song_title=_safe_str(row.get("song")),
        venue_name=_safe_str(row.get("venue")),
        notes=_safe_str(row.get("notes")),
    )


def _phishnet_review(row: dict[str, Any]) -> Review:
    return Review(
        review_id=_safe_str(row.get("reviewid")),
        show_id=_safe_str(row.get("showid")),
        date=_safe_str(row.get("showdate")),
        author=_safe_str(row.get("username") or row.get("author")),
        posted_at=_safe_str(row.get("posted_at")) or None,
        rating=_safe_float(row.get("score") or row.get("rating")),
        text=_safe_str(row.get("review") or row.get("text")),
    )


# ---- phish.in projections -------------------------------------------------


def _phishin_track(row: dict[str, Any], show_id: str | None = None) -> Track:
    sid = show_id
    if sid is None:
        nested = row.get("show")
        if isinstance(nested, dict):
            sid = _safe_str(nested.get("id"))
    return Track(
        track_id=_safe_int(row.get("id")),
        slug=_safe_str(row.get("slug")),
        title=_safe_str(row.get("title")),
        show_id=_safe_str(sid),
        show_date=_safe_str(row.get("show_date")),
        set_name=_safe_str(row.get("set_name")),
        position=_safe_int(row.get("position")),
        duration_ms=_safe_int(row.get("duration")),
        mp3_url=row.get("mp3_url"),
        waveform_image_url=row.get("waveform_image_url"),
        venue_name=_safe_str(row.get("venue_name")),
        venue_location=_safe_str(row.get("venue_location")),
    )


def _phishin_show_audio(row: dict[str, Any]) -> ShowAudio:
    show_id = _safe_str(row.get("id"))
    venue = row.get("venue") or {}
    venue_location = _safe_str(venue.get("location")) if isinstance(venue, dict) else ""
    cover = row.get("cover_art_urls") or {}
    cover_url: str | None = None
    if isinstance(cover, dict):
        cover_url = cover.get("large") or cover.get("medium") or cover.get("small")
    tracks_raw = row.get("tracks") or []
    tracks = [_phishin_track(t, show_id=show_id) for t in tracks_raw]
    return ShowAudio(
        show_id=show_id,
        date=_safe_str(row.get("date")),
        venue_name=_safe_str(row.get("venue_name")),
        venue_location=venue_location,
        duration_ms=_safe_int(row.get("duration")),
        audio_status=_safe_str(row.get("audio_status")),
        album_zip_url=row.get("album_zip_url"),
        cover_art_url=cover_url,
        tracks=tracks,
    )


# ---------------------------------------------------------------------------
# Cache key helpers
# ---------------------------------------------------------------------------


def _ckey_phishnet(method: str, **params: Any) -> tuple[str, dict[str, Any]]:
    return (f"phishnet:{method}", params)


def _ckey_phishin(method: str, **params: Any) -> tuple[str, dict[str, Any]]:
    return (f"phishin:{method}", params)


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------


def build_server(
    settings: Settings,
    *,
    phishnet_client: _PhishNetLike | None = None,
    phishin_client: _PhishInLike | None = None,
    cache: ResponseCache | None = None,
    phishnet_throttle: TokenBucket | None = None,
    phishin_throttle: TokenBucket | None = None,
) -> FastMCP:
    """Build a fully-wired FastMCP instance.

    Tests can pass in their own stubs/throttles/cache to keep behavior
    isolated. Production calls ``main()`` which constructs the live wiring.
    """
    pn_throttle = phishnet_throttle or TokenBucket(rps=settings.throttle_phishnet_rps)
    pi_throttle = phishin_throttle or TokenBucket(rps=settings.throttle_phishin_rps)

    pn: _PhishNetLike
    pi: _PhishInLike
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

    mcp = FastMCP("Phish")
    started_at = time.time()

    async def _cached_phishnet(endpoint: str, params: dict[str, Any], call: Any) -> Any:
        await response_cache.init()
        cache_key, cache_params = _ckey_phishnet(endpoint, **params)
        hit = await response_cache.get(cache_key, cache_params)
        if hit is not None:
            return hit
        payload = await call()
        await response_cache.put(cache_key, cache_params, payload)
        return payload

    async def _cached_phishin(endpoint: str, params: dict[str, Any], call: Any) -> Any:
        await response_cache.init()
        cache_key, cache_params = _ckey_phishin(endpoint, **params)
        hit = await response_cache.get(cache_key, cache_params)
        if hit is not None:
            return hit
        payload = await call()
        await response_cache.put(cache_key, cache_params, payload)
        return payload

    # ------------------------------------------------------------------
    # phish.net tools
    # ------------------------------------------------------------------

    @mcp.tool()
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
        try:
            payload = await _cached_phishnet(
                "search_shows", params, lambda: pn.search_shows(params)
            )
            rows = payload if isinstance(payload, list) else []
            summaries = [_phishnet_show_summary(row) for row in rows[:limit]]
            return _ok(summaries)
        except PhishNetError as exc:
            logger.exception("search_shows failed")
            return _err(str(exc), "UPSTREAM_DOWN", upstream="phish.net")

    @mcp.tool()
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
        try:
            if is_date:
                show_payload = await _cached_phishnet(
                    "get_show_by_date",
                    {"date": date_or_id},
                    lambda: pn.get_show_by_date(date_or_id),
                )
                setlist_payload = await _cached_phishnet(
                    "get_setlist_by_date",
                    {"date": date_or_id},
                    lambda: pn.get_setlist_by_date(date_or_id),
                )
            else:
                show_payload = await _cached_phishnet(
                    "get_show_by_id",
                    {"id": date_or_id},
                    lambda: pn.get_show_by_id(date_or_id),
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
                    setlist_payload = await _cached_phishnet(
                        "get_setlist_by_date",
                        {"date": date_for_setlist},
                        lambda: pn.get_setlist_by_date(date_for_setlist),
                    )
            rows = show_payload if isinstance(show_payload, list) else [show_payload]
            if not rows:
                return _err(f"show not found: {date_or_id}", "NOT_FOUND")
            setlist_rows = setlist_payload if isinstance(setlist_payload, list) else []
            return _ok(_phishnet_show_full(rows[0], setlist_rows))
        except PhishNetError as exc:
            logger.exception("get_show failed", extra={"date_or_id": date_or_id})
            code = "NOT_FOUND" if "no show" in str(exc).lower() else "UPSTREAM_DOWN"
            return _err(str(exc), code, upstream="phish.net")

    @mcp.tool()
    async def recent_shows(limit: int = 10) -> str:
        """List the most recent Phish shows.

        Args:
            limit: Max rows to return. Default 10. Capped at 100.

        Returns:
            JSON ``{"data": [ShowSummary, ...]}``, ordered most-recent-first.

        Idempotent. Example: ``recent_shows(limit=5)``.
        """
        capped = max(1, min(int(limit), 100))
        params: dict[str, Any] = {"order_by": "showdate.desc", "limit": capped}
        try:
            payload = await _cached_phishnet(
                "recent_shows", params, lambda: pn.search_shows(params)
            )
            rows = payload if isinstance(payload, list) else []
            # Sort defensively: stub may not respect order_by.
            rows = sorted(rows, key=lambda r: _safe_str(r.get("showdate")), reverse=True)
            summaries = [_phishnet_show_summary(row) for row in rows[:capped]]
            return _ok(summaries)
        except PhishNetError as exc:
            logger.exception("recent_shows failed")
            return _err(str(exc), "UPSTREAM_DOWN", upstream="phish.net")

    @mcp.tool()
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
        params = {"query": query}
        try:
            payload = await _cached_phishnet(
                "search_songs", params, lambda: pn.search_songs(params)
            )
            rows = payload if isinstance(payload, list) else []
            return _ok([_phishnet_song_summary(row) for row in rows[:limit]])
        except PhishNetError as exc:
            logger.exception("search_songs failed")
            return _err(str(exc), "UPSTREAM_DOWN", upstream="phish.net")

    @mcp.tool()
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
        try:
            payload = await _cached_phishnet(
                "get_song", {"slug": slug}, lambda: pn.get_song_by_slug(slug)
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
        try:
            payload = await _cached_phishnet(
                "song_history",
                {"slug": slug},
                lambda: pn.song_performances(slug),
            )
            rows = payload if isinstance(payload, list) else []
            rows_sorted = sorted(rows, key=lambda r: _safe_str(r.get("showdate")), reverse=True)
            return _ok([_phishnet_performance(row) for row in rows_sorted[:capped]])
        except PhishNetError as exc:
            logger.exception("song_history failed", extra={"slug": slug})
            return _err(str(exc), "UPSTREAM_DOWN", upstream="phish.net")

    @mcp.tool()
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
        params: dict[str, Any] = {}
        if year is not None:
            params["year"] = year
        try:
            payload = await _cached_phishnet(
                "jam_chart", params, lambda: pn.jam_chart(params or None)
            )
            rows = payload if isinstance(payload, list) else []
            return _ok([_phishnet_jam(row) for row in rows[:capped]])
        except PhishNetError as exc:
            logger.exception("jam_chart failed")
            return _err(str(exc), "UPSTREAM_DOWN", upstream="phish.net")

    @mcp.tool()
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
        try:
            if is_date:
                payload = await _cached_phishnet(
                    "get_reviews_by_date",
                    {"date": show_id_or_date},
                    lambda: pn.reviews_by_date(show_id_or_date),
                )
            else:
                payload = await _cached_phishnet(
                    "get_reviews_by_id",
                    {"id": show_id_or_date},
                    lambda: pn.reviews_by_id(show_id_or_date),
                )
            rows = payload if isinstance(payload, list) else []
            return _ok([_phishnet_review(row) for row in rows[:capped]])
        except PhishNetError as exc:
            logger.exception("get_reviews failed")
            return _err(str(exc), "UPSTREAM_DOWN", upstream="phish.net")

    # ------------------------------------------------------------------
    # phish.in tools
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_audio(show_id_or_date: str) -> str:
        """Fetch the audio bundle for a show: track list, MP3 URLs, durations.

        Source is phish.in. Older or rare shows may have ``audio_status``
        equal to ``"missing"`` or ``"partial"``.

        Args:
            show_id_or_date: YYYY-MM-DD or phish.in numeric show id.

        Returns:
            JSON ``{"data": ShowAudio}``. ShowAudio fields: show_id, date,
            venue_name, venue_location, duration_ms, audio_status,
            album_zip_url, cover_art_url, tracks[].

        Idempotent. Example: ``get_audio("1997-11-17")``.
        """
        if not show_id_or_date:
            return _err("show_id_or_date is required", "INVALID_INPUT")
        try:
            payload = await _cached_phishin(
                "get_show",
                {"key": show_id_or_date},
                lambda: pi.get_show(show_id_or_date),
            )
            if not payload:
                return _err(f"show not found: {show_id_or_date}", "NOT_FOUND")
            return _ok(_phishin_show_audio(cast(dict[str, Any], payload)))
        except PhishInError as exc:
            logger.exception("get_audio failed", extra={"key": show_id_or_date})
            code = "NOT_FOUND" if "no show" in str(exc).lower() else "UPSTREAM_DOWN"
            return _err(str(exc), code, upstream="phish.in")

    @mcp.tool()
    async def get_track(track_id: int) -> str:
        """Fetch one phish.in track by its numeric id.

        Args:
            track_id: phish.in track id (integer).

        Returns:
            JSON ``{"data": Track}``. Track fields: track_id, slug, title,
            show_id, show_date, set_name, position, duration_ms, mp3_url,
            waveform_image_url, venue_name, venue_location.

        Idempotent. Example: ``get_track(60001)``.
        """
        if not track_id:
            return _err("track_id is required", "INVALID_INPUT")
        try:
            payload = await _cached_phishin(
                "get_track", {"id": int(track_id)}, lambda: pi.get_track(int(track_id))
            )
            if not payload:
                return _err(f"track not found: {track_id}", "NOT_FOUND")
            return _ok(_phishin_track(cast(dict[str, Any], payload)))
        except PhishInError as exc:
            logger.exception("get_track failed", extra={"track_id": track_id})
            code = "NOT_FOUND" if "no track" in str(exc).lower() else "UPSTREAM_DOWN"
            return _err(str(exc), code, upstream="phish.in")

    @mcp.tool()
    async def search_audio_tracks(song_slug: str, limit: int = 20) -> str:
        """Find every phish.in audio track for a given song slug, across shows.

        Useful for "give me every recorded version of X" questions.

        Args:
            song_slug: phish.net/phish.in slug (e.g. ``"tweezer"``).
            limit: Max rows. Default 20, capped at 200.

        Returns:
            JSON ``{"data": [Track, ...]}``.

        Idempotent. Example: ``search_audio_tracks("tweezer", limit=5)``.
        """
        if not song_slug:
            return _err("song_slug is required", "INVALID_INPUT")
        capped = max(1, min(int(limit), 200))
        params = {"slug": song_slug, "per_page": capped}
        try:
            payload = await _cached_phishin(
                "search_tracks", params, lambda: pi.search_tracks(params)
            )
            rows = payload.get("tracks") or [] if isinstance(payload, dict) else []
            return _ok([_phishin_track(row) for row in rows[:capped]])
        except PhishInError as exc:
            logger.exception("search_audio_tracks failed", extra={"slug": song_slug})
            return _err(str(exc), "UPSTREAM_DOWN", upstream="phish.in")

    # ------------------------------------------------------------------
    # Meta tool
    # ------------------------------------------------------------------

    @mcp.tool()
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
            await response_cache.init()

        pn_snap = pn_throttle.snapshot()
        pi_snap = pi_throttle.snapshot()

        report = Health(
            status="ok",
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
                size_bytes=response_cache.size_bytes(),
                ttl_seconds=settings.cache_ttl_seconds,
                last_hit_ts=_iso(response_cache.last_hit_ts),
                last_miss_ts=_iso(response_cache.last_miss_ts),
            ),
        )
        # Surface uptime in extras for log-side observability.
        logger.debug("health snapshot", extra={"uptime_s": int(time.time() - started_at)})
        return _ok(report)

    return mcp


# ---------------------------------------------------------------------------
# Module-level entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entrypoint used by the Docker image."""
    settings = load_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    logger.info("MCP Phish starting", extra={"config": settings.safe_repr()})
    server = build_server(settings)
    server.run(
        transport="streamable-http",
        host=settings.mcp_host,
        port=settings.mcp_port,
    )


if __name__ == "__main__":
    main()
