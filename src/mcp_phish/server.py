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
from typing import TYPE_CHECKING, Any, Protocol, cast

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
    SongGap,
    SongSummary,
    Track,
    UpstreamHealth,
    VaultHealth,
    Venue,
    VenueShow,
)
from mcp_phish.throttle import TokenBucket
from mcp_phish.vault import VaultReader

if TYPE_CHECKING:
    import asyncpg

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
# Vault projection helpers (asyncpg.Record → frozen Pydantic models)
#
# These mirror the _phishnet_* and _phishin_* helpers above but read from
# vault rows instead of upstream API dicts. The output shapes are identical.
# ---------------------------------------------------------------------------


def _vault_show_summary(row: Any) -> ShowSummary:
    show_id = str(row["show_id_phishin"] or row.get("show_id_phishnet") or "")
    return ShowSummary(
        show_id=show_id,
        date=str(row["date"]),
        venue_name=_safe_str(row.get("venue_name")),
        location=_safe_str(row.get("location")),
        tour_name=_safe_str(row.get("tour_name")),
    )


def _vault_show_full(show_row: Any, setlist_rows: list[Any]) -> Show:
    set_label_map = {"1": "Set 1", "2": "Set 2", "3": "Set 3", "e": "Encore"}
    setlist = [
        SetlistEntry(
            position=_safe_int(row.get("position")),
            set_name=set_label_map.get(
                _safe_str(row.get("set_label")), _safe_str(row.get("set_label"))
            ),
            song_slug=_safe_str(row.get("song_slug")),
            song_title=_safe_str(row.get("song_name")),
            transition=_safe_str(row.get("transition")).strip(),
            footnote=_safe_str(row.get("footnote")),
        )
        for row in setlist_rows
    ]
    venue = Venue(
        slug=_safe_str(show_row.get("venue_slug")),
        name=_safe_str(show_row.get("venue_name")),
        city=_safe_str(show_row.get("city")),
        state=_safe_str(show_row.get("state")),
        country=_safe_str(show_row.get("country")),
        location=_safe_str(show_row.get("location")),
        latitude=_safe_float(show_row.get("latitude")),
        longitude=_safe_float(show_row.get("longitude")),
    )
    show_id = str(show_row["show_id_phishin"] or show_row.get("show_id_phishnet") or "")
    return Show(
        show_id=show_id,
        date=str(show_row["date"]),
        venue=venue,
        tour_name=_safe_str(show_row.get("tour_name")),
        setlist=setlist,
    )


def _vault_song_summary(row: Any) -> SongSummary:
    return SongSummary(
        slug=_safe_str(row.get("slug")),
        title=_safe_str(row.get("title")),
        artist=row.get("artist") or None,
        original=bool(row.get("original", True)),
        times_played=_safe_int(row.get("times_played")),
    )


def _vault_song_full(row: Any) -> Song:
    debut = row.get("debut_date")
    last_played = row.get("last_play_date")
    gap = row.get("gap_current")
    return Song(
        slug=_safe_str(row.get("slug")),
        title=_safe_str(row.get("title")),
        artist=row.get("artist") or None,
        original=bool(row.get("original", True)),
        times_played=_safe_int(row.get("times_played")),
        debut_date=str(debut) if debut is not None else None,
        last_played_date=str(last_played) if last_played is not None else None,
        gap=_safe_int(gap) if gap is not None else None,
    )


def _vault_performance(row: Any) -> Performance:
    show_id = str(row.get("show_id_phishin") or row.get("show_id_phishnet") or "")
    return Performance(
        show_id=show_id,
        date=str(row["date"]),
        venue_name=_safe_str(row.get("venue_name")),
        location=_safe_str(row.get("venue_location")),
        set_name=_safe_str(row.get("set_name")),
        gap=_safe_int(row["gap"]) if row.get("gap") is not None else None,
    )


def _vault_jam(row: Any) -> NotableJam:
    show_id = str(row.get("show_id_phishin") or row.get("show_id_phishnet") or "")
    return NotableJam(
        show_id=show_id,
        date=str(row["date"]),
        song_slug=_safe_str(row.get("song_slug")),
        song_title=_safe_str(row.get("song_name")),
        venue_name=_safe_str(row.get("venue_name")),
        notes=_safe_str(row.get("notes")),
    )


def _vault_review(row: Any) -> Review:
    posted = row.get("posted_at")
    if hasattr(posted, "isoformat"):
        posted_iso: str | None = posted.isoformat()
    elif posted:
        posted_iso = str(posted)
    else:
        posted_iso = None
    return Review(
        review_id=str(row.get("upstream_review_id") or row.get("id") or ""),
        show_id="",  # vault reviews are keyed by date; show_id not always present
        date=str(row["show_date"]),
        author=_safe_str(row.get("username")),
        posted_at=posted_iso,
        rating=_safe_float(row.get("score")),
        text=_safe_str(row.get("review_text")),
    )


def _vault_track(row: Any, show_id: str | None = None) -> Track:
    sid = show_id or str(row.get("show_id_phishin") or "")
    return Track(
        track_id=_safe_int(row.get("id")),
        slug=_safe_str(row.get("slug")),
        title=_safe_str(row.get("title")),
        show_id=sid,
        show_date=str(row.get("show_date") or ""),
        set_name=_safe_str(row.get("set_name")),
        position=_safe_int(row.get("position")),
        duration_ms=_safe_int(row.get("duration_ms")),
        mp3_url=row.get("mp3_url"),
        waveform_image_url=row.get("waveform_image_url"),
        venue_name=_safe_str(row.get("venue_name")),
        venue_location=_safe_str(row.get("venue_location")),
    )


def _vault_show_audio(show_row: Any, tracks: list[Any]) -> ShowAudio:
    show_id = str(show_row.get("show_id_phishin") or "")
    return ShowAudio(
        show_id=show_id,
        date=str(show_row["date"]),
        venue_name=_safe_str(show_row.get("venue_name")),
        venue_location=_safe_str(show_row.get("venue_location")),
        duration_ms=_safe_int(show_row.get("duration_ms")),
        audio_status=_safe_str(show_row.get("audio_status")),
        album_zip_url=show_row.get("album_zip_url"),
        cover_art_url=show_row.get("cover_art_url_large"),
        tracks=[_vault_track(t, show_id=show_id) for t in tracks],
    )


def _vault_venue_show(row: Any) -> VenueShow:
    show_id = str(row.get("show_id_phishin") or row.get("show_id_phishnet") or "")
    return VenueShow(
        show_id=show_id,
        date=str(row["date"]),
        venue_name=_safe_str(row.get("venue_name")),
        location=_safe_str(row.get("location")),
        tour_name=_safe_str(row.get("tour_name")),
    )


def _vault_song_gap(row: Any) -> SongGap:
    last_played = row.get("last_play_date")
    return SongGap(
        slug=_safe_str(row.get("slug")),
        title=_safe_str(row.get("title")),
        times_played=_safe_int(row.get("times_played")),
        gap_current=_safe_int(row.get("gap_current")),
        last_played_date=str(last_played) if last_played is not None else None,
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
    vault_reader: VaultReader | None = None,
    vault_pool: asyncpg.Pool | None = None,
) -> FastMCP:
    """Build a fully-wired FastMCP instance.

    Tests can pass in their own stubs/throttles/cache/vault_reader to keep
    behavior isolated. Production calls ``main()`` which relies on lazy-init
    for the vault pool.

    ``vault_reader`` takes precedence over ``vault_pool`` when both are given.
    When ``vault_pool`` is given, a ``VaultReader`` is constructed from it.
    When neither is given and ``settings.vault_enabled`` is True, the pool is
    created lazily on the first vault read.
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

    # --- vault reader setup ------------------------------------------------
    # Priority: explicit vault_reader > vault_pool > lazy-init on first use
    _vault_reader: VaultReader | None
    if vault_reader is not None:
        _vault_reader = vault_reader
    elif vault_pool is not None:
        _vault_reader = VaultReader(vault_pool)
    else:
        _vault_reader = None  # will be created lazily if vault_enabled

    _lazy_pool_holder: list[Any] = [None]  # mutable cell for lazy pool

    async def _get_vault_reader() -> VaultReader | None:
        """Return the VaultReader, lazily initialising the pool when needed."""
        if _vault_reader is not None:
            return _vault_reader
        if not settings.vault_enabled:
            return None
        # Lazy pool creation on first vault read.
        if _lazy_pool_holder[0] is None:
            try:
                import asyncpg as _asyncpg

                _lazy_pool_holder[0] = await _asyncpg.create_pool(
                    settings.pg_dsn,
                    min_size=1,
                    max_size=5,
                )
                logger.info("vault pool created", extra={"dsn_host": settings.pg_host})
            except Exception:
                logger.exception("failed to create vault pool")
                return None
        return VaultReader(_lazy_pool_holder[0])

    def _is_hot_window(date_str: str) -> bool:
        """Return True if show date is within vault_hot_window_hours of now."""
        try:
            show_dt = datetime.fromisoformat(date_str)
            if show_dt.tzinfo is None:
                show_dt = show_dt.replace(tzinfo=UTC)
            age_hours = (datetime.now(tz=UTC) - show_dt).total_seconds() / 3600
            return age_hours < settings.vault_hot_window_hours
        except (ValueError, OverflowError):
            return False

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
        vr = await _get_vault_reader()
        if vr is not None:
            try:
                rows = await vr.search_shows(
                    year=year, venue=venue, city=city,
                    state=state, country=country, limit=limit,
                )
                return _ok([_vault_show_summary(row) for row in rows])
            except Exception:
                logger.exception("vault search_shows failed; falling back to live")
        try:
            payload = await _cached_phishnet(
                "search_shows", params, lambda: pn.search_shows(params)
            )
            rows_live = payload if isinstance(payload, list) else []
            summaries = [_phishnet_show_summary(row) for row in rows_live[:limit]]
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
        vr = await _get_vault_reader()
        use_vault = vr is not None and not (is_date and _is_hot_window(date_or_id))
        if use_vault:
            assert vr is not None
            try:
                show_row, setlist_rows = await vr.get_show(date_or_id)
                if show_row is None:
                    return _err(f"show not found: {date_or_id}", "NOT_FOUND")
                return _ok(_vault_show_full(show_row, setlist_rows))
            except Exception:
                logger.exception("vault get_show failed; falling back to live",
                                 extra={"date_or_id": date_or_id})
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
            setlist_rows_live = setlist_payload if isinstance(setlist_payload, list) else []
            return _ok(_phishnet_show_full(rows[0], setlist_rows_live))
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
        vr = await _get_vault_reader()
        if vr is not None:
            try:
                rows = await vr.recent_shows(limit=capped)
                return _ok([_vault_show_summary(row) for row in rows])
            except Exception:
                logger.exception("vault recent_shows failed; falling back to live")
        params: dict[str, Any] = {"order_by": "showdate.desc", "limit": capped}
        try:
            payload = await _cached_phishnet(
                "recent_shows", params, lambda: pn.search_shows(params)
            )
            rows_live = payload if isinstance(payload, list) else []
            # Sort defensively: stub may not respect order_by.
            rows_live = sorted(
                rows_live, key=lambda r: _safe_str(r.get("showdate")), reverse=True
            )
            summaries = [_phishnet_show_summary(row) for row in rows_live[:capped]]
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
        vr = await _get_vault_reader()
        if vr is not None:
            try:
                rows = await vr.search_songs(query=query, limit=limit)
                return _ok([_vault_song_summary(row) for row in rows])
            except Exception:
                logger.exception("vault search_songs failed; falling back to live")
        params = {"query": query}
        try:
            payload = await _cached_phishnet(
                "search_songs", params, lambda: pn.search_songs(params)
            )
            rows_live = payload if isinstance(payload, list) else []
            return _ok([_phishnet_song_summary(row) for row in rows_live[:limit]])
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
        vr = await _get_vault_reader()
        if vr is not None:
            try:
                row = await vr.get_song(slug)
                if row is None:
                    return _err(f"song not found: {slug}", "NOT_FOUND")
                return _ok(_vault_song_full(row))
            except Exception:
                logger.exception("vault get_song failed; falling back to live",
                                 extra={"slug": slug})
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
        vr = await _get_vault_reader()
        if vr is not None:
            try:
                rows = await vr.song_history(slug=slug, limit=capped)
                return _ok([_vault_performance(row) for row in rows])
            except Exception:
                logger.exception("vault song_history failed; falling back to live",
                                 extra={"slug": slug})
        try:
            payload = await _cached_phishnet(
                "song_history",
                {"slug": slug},
                lambda: pn.song_performances(slug),
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
        vr = await _get_vault_reader()
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
            payload = await _cached_phishnet(
                "jam_chart", params, lambda: pn.jam_chart(params or None)
            )
            rows_live = payload if isinstance(payload, list) else []
            return _ok([_phishnet_jam(row) for row in rows_live[:capped]])
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
        vr = await _get_vault_reader()
        # Vault reviews are indexed by show date only; skip vault for numeric ids.
        if vr is not None and is_date:
            try:
                rows = await vr.get_reviews(show_date=show_id_or_date, limit=capped)
                return _ok([_vault_review(row) for row in rows])
            except Exception:
                logger.exception("vault get_reviews failed; falling back to live")
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
            rows_live = payload if isinstance(payload, list) else []
            return _ok([_phishnet_review(row) for row in rows_live[:capped]])
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
        is_date_key = len(show_id_or_date) == 10 and show_id_or_date.count("-") == 2
        vr = await _get_vault_reader()
        use_vault = vr is not None and not (
            is_date_key and _is_hot_window(show_id_or_date)
        )
        if use_vault:
            assert vr is not None
            try:
                show_row, tracks = await vr.get_audio(show_id_or_date)
                if show_row is None:
                    return _err(f"show not found: {show_id_or_date}", "NOT_FOUND")
                return _ok(_vault_show_audio(show_row, tracks))
            except Exception:
                logger.exception("vault get_audio failed; falling back to live",
                                 extra={"key": show_id_or_date})
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
        vr = await _get_vault_reader()
        if vr is not None:
            try:
                row = await vr.get_track(int(track_id))
                if row is None:
                    return _err(f"track not found: {track_id}", "NOT_FOUND")
                return _ok(_vault_track(row))
            except Exception:
                logger.exception("vault get_track failed; falling back to live",
                                 extra={"track_id": track_id})
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
        vr = await _get_vault_reader()
        if vr is not None:
            try:
                rows = await vr.search_audio_tracks(song_slug=song_slug, limit=capped)
                return _ok([_vault_track(row) for row in rows])
            except Exception:
                logger.exception("vault search_audio_tracks failed; falling back to live",
                                 extra={"slug": song_slug})
        params = {"slug": song_slug, "per_page": capped}
        try:
            payload = await _cached_phishin(
                "search_tracks", params, lambda: pi.search_tracks(params)
            )
            rows_live = payload.get("tracks") or [] if isinstance(payload, dict) else []
            return _ok([_phishin_track(row) for row in rows_live[:capped]])
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

        # Build vault health snapshot.
        vault_health_status = "ok"
        vault_h: VaultHealth
        vr = await _get_vault_reader()
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
                size_bytes=response_cache.size_bytes(),
                ttl_seconds=settings.cache_ttl_seconds,
                last_hit_ts=_iso(response_cache.last_hit_ts),
                last_miss_ts=_iso(response_cache.last_miss_ts),
            ),
            vault=vault_h,
        )
        # Surface uptime in extras for log-side observability.
        logger.debug("health snapshot", extra={"uptime_s": int(time.time() - started_at)})
        return _ok(report)

    # ------------------------------------------------------------------
    # Vault-only analytical tools
    # ------------------------------------------------------------------

    @mcp.tool()
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
        vr = await _get_vault_reader()
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
        vr = await _get_vault_reader()
        if vr is None:
            return _err("songs_by_gap requires vault (VAULT_ENABLED=true)", "VAULT_DISABLED")
        capped = max(1, min(int(limit), 200))
        try:
            rows = await vr.songs_by_gap(limit=capped)
            return _ok([_vault_song_gap(row) for row in rows])
        except Exception as exc:
            logger.exception("songs_by_gap failed")
            return _err(str(exc), "VAULT_ERROR")

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
