"""Postgres vault rows → frozen public models.

These mirror the phish.net and phish.in mappers but read vault column names
instead of upstream JSON keys. The output shapes are identical by design: a
caller cannot tell from the wire format whether a response came from the
vault or from a live upstream, which is the whole point of the vault swap.

Rows are typed ``Any`` because they are ``asyncpg.Record`` in production and
plain dicts in tests; both support ``__getitem__`` and ``.get``.
"""

from __future__ import annotations

from typing import Any

from mcp_phish.mappers.coerce import safe_float, safe_int, safe_str
from mcp_phish.models import (
    DebutSong,
    LongShow,
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
    StatsOverview,
    TopSong,
    Track,
    Venue,
    VenueShow,
)

__all__ = [
    "jam",
    "performance",
    "review",
    "show_audio",
    "show_full",
    "show_summary",
    "song_full",
    "song_gap",
    "song_summary",
    "stats_overview",
    "track",
    "venue_show",
]

_SET_LABELS = {"1": "Set 1", "2": "Set 2", "3": "Set 3", "e": "Encore"}


def _show_id(row: Any) -> str:
    """Prefer the phish.in show id, fall back to the phish.net one."""
    return str(row.get("show_id_phishin") or row.get("show_id_phishnet") or "")


def show_summary(row: Any) -> ShowSummary:
    show_id = str(row["show_id_phishin"] or row.get("show_id_phishnet") or "")
    return ShowSummary(
        show_id=show_id,
        date=str(row["date"]),
        venue_name=safe_str(row.get("venue_name")),
        location=safe_str(row.get("location")),
        tour_name=safe_str(row.get("tour_name")),
    )


def show_full(show_row: Any, setlist_rows: list[Any]) -> Show:
    setlist = [
        SetlistEntry(
            position=safe_int(row.get("position")),
            set_name=_SET_LABELS.get(
                safe_str(row.get("set_label")), safe_str(row.get("set_label"))
            ),
            song_slug=safe_str(row.get("song_slug")),
            song_title=safe_str(row.get("song_name")),
            transition=safe_str(row.get("transition")).strip(),
            footnote=safe_str(row.get("footnote")),
        )
        for row in setlist_rows
    ]
    venue = Venue(
        slug=safe_str(show_row.get("venue_slug")),
        name=safe_str(show_row.get("venue_name")),
        city=safe_str(show_row.get("city")),
        state=safe_str(show_row.get("state")),
        country=safe_str(show_row.get("country")),
        location=safe_str(show_row.get("location")),
        latitude=safe_float(show_row.get("latitude")),
        longitude=safe_float(show_row.get("longitude")),
    )
    show_id = str(show_row["show_id_phishin"] or show_row.get("show_id_phishnet") or "")
    return Show(
        show_id=show_id,
        date=str(show_row["date"]),
        venue=venue,
        tour_name=safe_str(show_row.get("tour_name")),
        setlist=setlist,
    )


def song_summary(row: Any) -> SongSummary:
    gap = row.get("gap_current")
    return SongSummary(
        slug=safe_str(row.get("slug")),
        title=safe_str(row.get("title")),
        artist=row.get("artist") or None,
        original=bool(row.get("original", True)),
        times_played=safe_int(row.get("times_played")),
        gap=safe_int(gap) if gap is not None else None,
    )


def song_full(row: Any) -> Song:
    debut = row.get("debut_date")
    last_played = row.get("last_play_date")
    gap = row.get("gap_current")
    return Song(
        slug=safe_str(row.get("slug")),
        title=safe_str(row.get("title")),
        artist=row.get("artist") or None,
        original=bool(row.get("original", True)),
        times_played=safe_int(row.get("times_played")),
        debut_date=str(debut) if debut is not None else None,
        last_played_date=str(last_played) if last_played is not None else None,
        gap=safe_int(gap) if gap is not None else None,
    )


def performance(row: Any) -> Performance:
    return Performance(
        show_id=_show_id(row),
        date=str(row["date"]),
        venue_name=safe_str(row.get("venue_name")),
        location=safe_str(row.get("venue_location")),
        set_name=safe_str(row.get("set_name")),
        gap=safe_int(row["gap"]) if row.get("gap") is not None else None,
    )


def jam(row: Any) -> NotableJam:
    return NotableJam(
        show_id=_show_id(row),
        date=str(row["date"]),
        song_slug=safe_str(row.get("song_slug")),
        song_title=safe_str(row.get("song_name")),
        venue_name=safe_str(row.get("venue_name")),
        notes=safe_str(row.get("notes")),
    )


def review(row: Any) -> Review:
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
        author=safe_str(row.get("username")),
        posted_at=posted_iso,
        rating=safe_float(row.get("score")),
        text=safe_str(row.get("review_text")),
    )


def track(row: Any, show_id: str | None = None) -> Track:
    sid = show_id or str(row.get("show_id_phishin") or "")
    return Track(
        track_id=safe_int(row.get("id")),
        slug=safe_str(row.get("slug")),
        title=safe_str(row.get("title")),
        show_id=sid,
        show_date=str(row.get("show_date") or ""),
        set_name=safe_str(row.get("set_name")),
        position=safe_int(row.get("position")),
        duration_ms=safe_int(row.get("duration_ms")),
        mp3_url=row.get("mp3_url"),
        waveform_image_url=row.get("waveform_image_url"),
        venue_name=safe_str(row.get("venue_name")),
        venue_location=safe_str(row.get("venue_location")),
    )


def show_audio(show_row: Any, tracks: list[Any]) -> ShowAudio:
    show_id = str(show_row.get("show_id_phishin") or "")
    return ShowAudio(
        show_id=show_id,
        date=str(show_row["date"]),
        venue_name=safe_str(show_row.get("venue_name")),
        venue_location=safe_str(show_row.get("venue_location")),
        duration_ms=safe_int(show_row.get("duration_ms")),
        audio_status=safe_str(show_row.get("audio_status")),
        album_zip_url=show_row.get("album_zip_url"),
        cover_art_url=show_row.get("cover_art_url_large"),
        tracks=[track(t, show_id=show_id) for t in tracks],
    )


def venue_show(row: Any) -> VenueShow:
    return VenueShow(
        show_id=_show_id(row),
        date=str(row["date"]),
        venue_name=safe_str(row.get("venue_name")),
        location=safe_str(row.get("location")),
        tour_name=safe_str(row.get("tour_name")),
    )


def song_gap(row: Any) -> SongGap:
    last_played = row.get("last_play_date")
    return SongGap(
        slug=safe_str(row.get("slug")),
        title=safe_str(row.get("title")),
        times_played=safe_int(row.get("times_played")),
        gap_current=safe_int(row.get("gap_current")),
        last_played_date=str(last_played) if last_played is not None else None,
    )


def stats_overview(raw: dict[str, Any]) -> StatsOverview:
    """Project the vault's aggregate roll-up dict into the public model."""
    return StatsOverview(
        total_shows=safe_int(raw.get("total_shows")),
        total_songs_tracked=safe_int(raw.get("total_songs_tracked")),
        distinct_songs_played=safe_int(raw.get("distinct_songs_played")),
        total_performances=safe_int(raw.get("total_performances")),
        avg_songs_per_show=safe_float(raw.get("avg_songs_per_show")) or 0.0,
        first_show_date=safe_str(raw.get("first_show_date")) or None,
        last_show_date=safe_str(raw.get("last_show_date")) or None,
        most_played=[
            TopSong(
                slug=safe_str(r.get("slug")),
                title=safe_str(r.get("title")),
                times_played=safe_int(r.get("times_played")),
            )
            for r in raw.get("most_played", [])
        ],
        biggest_gaps=[
            SongGap(
                slug=safe_str(r.get("slug")),
                title=safe_str(r.get("title")),
                gap_current=safe_int(r.get("gap_current")),
                times_played=safe_int(r.get("times_played")),
                last_played_date=safe_str(r.get("last_play_date")) or None,
            )
            for r in raw.get("biggest_gaps", [])
        ],
        rarest_songs=[
            TopSong(
                slug=safe_str(r.get("slug")),
                title=safe_str(r.get("title")),
                times_played=safe_int(r.get("times_played")),
            )
            for r in raw.get("rarest_songs", [])
        ],
        recent_debuts=[
            DebutSong(
                slug=safe_str(r.get("slug")),
                title=safe_str(r.get("title")),
                debut_date=safe_str(r.get("debut_date")) or None,
                times_played=safe_int(r.get("times_played")),
            )
            for r in raw.get("recent_debuts", [])
        ],
        longest_shows=[
            LongShow(
                show_id=safe_str(r.get("show_id")),
                date=safe_str(r.get("date")),
                venue_name=safe_str(r.get("venue_name")),
                location=safe_str(r.get("location")),
                song_count=safe_int(r.get("song_count")),
            )
            for r in raw.get("longest_shows", [])
        ],
    )
