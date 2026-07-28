"""phish.in v2 JSON dicts → frozen public models.

phish.in is the audio source: tracks, MP3 URLs, durations, cover art. Its
field names do not overlap with phish.net's, which is why it gets its own
module rather than a shared "show" mapper.
"""

from __future__ import annotations

from typing import Any

from mcp_phish.mappers.coerce import safe_int, safe_str
from mcp_phish.models import ShowAudio, Track

__all__ = ["show_audio", "track"]


def track(row: dict[str, Any], show_id: str | None = None) -> Track:
    sid = show_id
    if sid is None:
        nested = row.get("show")
        if isinstance(nested, dict):
            sid = safe_str(nested.get("id"))
    return Track(
        track_id=safe_int(row.get("id")),
        slug=safe_str(row.get("slug")),
        title=safe_str(row.get("title")),
        show_id=safe_str(sid),
        show_date=safe_str(row.get("show_date")),
        set_name=safe_str(row.get("set_name")),
        position=safe_int(row.get("position")),
        duration_ms=safe_int(row.get("duration")),
        mp3_url=row.get("mp3_url"),
        waveform_image_url=row.get("waveform_image_url"),
        venue_name=safe_str(row.get("venue_name")),
        venue_location=safe_str(row.get("venue_location")),
    )


def show_audio(row: dict[str, Any]) -> ShowAudio:
    show_id = safe_str(row.get("id"))
    venue = row.get("venue") or {}
    venue_location = safe_str(venue.get("location")) if isinstance(venue, dict) else ""
    cover = row.get("cover_art_urls") or {}
    cover_url: str | None = None
    if isinstance(cover, dict):
        cover_url = cover.get("large") or cover.get("medium") or cover.get("small")
    tracks_raw = row.get("tracks") or []
    tracks = [track(t, show_id=show_id) for t in tracks_raw]
    return ShowAudio(
        show_id=show_id,
        date=safe_str(row.get("date")),
        venue_name=safe_str(row.get("venue_name")),
        venue_location=venue_location,
        duration_ms=safe_int(row.get("duration")),
        audio_status=safe_str(row.get("audio_status")),
        album_zip_url=row.get("album_zip_url"),
        cover_art_url=cover_url,
        tracks=tracks,
    )
