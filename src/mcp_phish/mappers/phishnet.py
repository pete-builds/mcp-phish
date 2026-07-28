"""api.phish.net v5 JSON dicts → frozen public models.

Real and stub phish.net clients return the same upstream shape, so this one
projection layer covers both modes.
"""

from __future__ import annotations

from typing import Any

from mcp_phish.mappers.coerce import safe_float, safe_int, safe_str
from mcp_phish.models import (
    NotableJam,
    Performance,
    Review,
    SetlistEntry,
    Show,
    ShowSummary,
    Song,
    SongSummary,
    Venue,
)

__all__ = [
    "jam",
    "location",
    "performance",
    "review",
    "show_full",
    "show_summary",
    "song_full",
    "song_summary",
]

_SET_LABELS = {"1": "Set 1", "2": "Set 2", "3": "Set 3", "e": "Encore"}


def location(row: dict[str, Any]) -> str:
    """Join ``city`` and ``state`` into the display location string."""
    city = safe_str(row.get("city"))
    state = safe_str(row.get("state"))
    if city and state:
        return f"{city}, {state}"
    return city or state


def show_summary(row: dict[str, Any]) -> ShowSummary:
    return ShowSummary(
        show_id=safe_str(row.get("showid")),
        date=safe_str(row.get("showdate")),
        venue_name=safe_str(row.get("venue")),
        location=location(row),
        tour_name=safe_str(row.get("tour_name")),
    )


def show_full(show_row: dict[str, Any], setlist_rows: list[dict[str, Any]]) -> Show:
    setlist = [
        SetlistEntry(
            position=safe_int(row.get("position")),
            set_name=_SET_LABELS.get(safe_str(row.get("set")), safe_str(row.get("set"))),
            song_slug=safe_str(row.get("slug")),
            song_title=safe_str(row.get("song")),
            transition=safe_str(row.get("trans_mark")).strip(),
            footnote=safe_str(row.get("footnote")),
        )
        for row in setlist_rows
    ]
    venue = Venue(
        slug="",
        name=safe_str(show_row.get("venue")),
        city=safe_str(show_row.get("city")),
        state=safe_str(show_row.get("state")),
        country=safe_str(show_row.get("country")),
        location=location(show_row),
    )
    return Show(
        show_id=safe_str(show_row.get("showid")),
        date=safe_str(show_row.get("showdate")),
        venue=venue,
        tour_name=safe_str(show_row.get("tour_name")),
        setlist=setlist,
        rating=safe_float(show_row.get("rating")),
        rating_count=safe_int(show_row.get("rating_count")),
        review_count=safe_int(show_row.get("review_count")),
        setlist_notes=safe_str(show_row.get("setlistnotes")),
    )


def song_summary(row: dict[str, Any]) -> SongSummary:
    is_original = bool(row.get("isoriginal", row.get("original", True)))
    return SongSummary(
        slug=safe_str(row.get("slug")),
        title=safe_str(row.get("title")),
        artist=row.get("artist") if row.get("artist") else None,
        original=is_original,
        times_played=safe_int(row.get("times_played")),
    )


def song_full(row: dict[str, Any]) -> Song:
    is_original = bool(row.get("isoriginal", row.get("original", True)))
    debut = row.get("debut") or row.get("debut_date")
    last_played = row.get("last_played") or row.get("last_played_date")
    gap = row.get("gap")
    return Song(
        slug=safe_str(row.get("slug")),
        title=safe_str(row.get("title")),
        artist=row.get("artist") if row.get("artist") else None,
        original=is_original,
        times_played=safe_int(row.get("times_played")),
        debut_date=safe_str(debut) if debut else None,
        last_played_date=safe_str(last_played) if last_played else None,
        gap=safe_int(gap) if gap is not None else None,
    )


def performance(row: dict[str, Any]) -> Performance:
    return Performance(
        show_id=safe_str(row.get("showid")),
        date=safe_str(row.get("showdate")),
        venue_name=safe_str(row.get("venue")),
        location=location(row),
        set_name=safe_str(row.get("set")),
        transition=safe_str(row.get("trans_mark")).strip(),
        gap=safe_int(row.get("gap")) if row.get("gap") is not None else None,
    )


def jam(row: dict[str, Any]) -> NotableJam:
    return NotableJam(
        show_id=safe_str(row.get("showid")),
        date=safe_str(row.get("showdate")),
        song_slug=safe_str(row.get("slug")),
        song_title=safe_str(row.get("song")),
        venue_name=safe_str(row.get("venue")),
        notes=safe_str(row.get("notes")),
    )


def review(row: dict[str, Any]) -> Review:
    return Review(
        review_id=safe_str(row.get("reviewid")),
        show_id=safe_str(row.get("showid")),
        date=safe_str(row.get("showdate")),
        author=safe_str(row.get("username") or row.get("author")),
        posted_at=safe_str(row.get("posted_at")) or None,
        rating=safe_float(row.get("score") or row.get("rating")),
        text=safe_str(row.get("review") or row.get("text")),
    )
