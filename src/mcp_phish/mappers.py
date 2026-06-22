"""Projection helpers: upstream/​vault rows → public Pydantic models.

Three families live here, all pure functions with no I/O:

* ``_safe_*``       — defensive scalar coercion
* ``_phishnet_*``   — api.phish.net row dicts → models
* ``_phishin_*``    — phish.in row dicts → models
* ``_vault_*``      — asyncpg.Record-like rows → models (Phase 3)

Both real and stub clients return the same upstream shape, so a single
projection layer covers stub mode, live mode, and the vault swap. The output
models are byte-identical across all three sources.

The ``_PhishNetLike`` / ``_PhishInLike`` protocols and the ``_ckey_*`` cache-key
helpers also live here so the tool modules import their typing + cache contracts
from one place.
"""

from __future__ import annotations

from typing import Any, Protocol

from mcp_phish.models import (
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
    Venue,
    VenueShow,
)

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
# Scalar coercion
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


# ---------------------------------------------------------------------------
# phish.net projections
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# phish.in projections
# ---------------------------------------------------------------------------


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
# Vault projections (asyncpg.Record → frozen Pydantic models)
#
# These mirror the _phishnet_* and _phishin_* helpers above but read from vault
# rows instead of upstream API dicts. The output shapes are identical.
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


__all__ = [
    "_PhishInLike",
    "_PhishNetLike",
    "_ckey_phishin",
    "_ckey_phishnet",
    "_phishin_show_audio",
    "_phishin_track",
    "_phishnet_jam",
    "_phishnet_location",
    "_phishnet_performance",
    "_phishnet_review",
    "_phishnet_show_full",
    "_phishnet_show_summary",
    "_phishnet_song_full",
    "_phishnet_song_summary",
    "_safe_float",
    "_safe_int",
    "_safe_str",
    "_vault_jam",
    "_vault_performance",
    "_vault_review",
    "_vault_show_audio",
    "_vault_show_full",
    "_vault_show_summary",
    "_vault_song_full",
    "_vault_song_gap",
    "_vault_song_summary",
    "_vault_track",
    "_vault_venue_show",
]
