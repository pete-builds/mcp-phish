"""Tests for the public Pydantic contract.

These models are FROZEN. Any change here breaks the Phase 3 vault swap
invariant. Treat failures as a contract regression, not a test bug.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

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


def test_show_summary_minimal() -> None:
    summary = ShowSummary(show_id="1", date="1995-12-30")
    assert summary.show_id == "1"
    assert summary.location == ""


def test_show_full_with_setlist() -> None:
    show = Show(
        show_id="1",
        date="1995-12-30",
        venue=Venue(name="MSG", city="New York", state="NY", location="New York, NY"),
        setlist=[
            SetlistEntry(
                position=1,
                set_name="Set 1",
                song_slug="reba",
                song_title="Reba",
                transition=">",
            )
        ],
        rating=4.7,
        rating_count=412,
    )
    assert show.setlist[0].song_slug == "reba"
    assert show.rating == 4.7


def test_models_reject_extra_fields() -> None:
    """`extra="forbid"` is the contract guard against silent drift."""
    with pytest.raises(ValidationError):
        ShowSummary(show_id="1", date="1995-12-30", extra_field="nope")  # type: ignore[call-arg]


def test_models_are_frozen() -> None:
    summary = ShowSummary(show_id="1", date="1995-12-30")
    with pytest.raises(ValidationError):
        summary.show_id = "2"  # type: ignore[misc]


def test_song_optional_fields_default_none() -> None:
    song = Song(slug="ghost", title="Ghost", times_played=312)
    assert song.debut_date is None
    assert song.artist is None


def test_song_summary_defaults_original_true() -> None:
    summary = SongSummary(slug="reba", title="Reba")
    assert summary.original is True


def test_track_serialization_roundtrip() -> None:
    track = Track(
        track_id=60001,
        slug="tweezer",
        title="Tweezer",
        show_id="412412",
        show_date="1997-11-17",
        set_name="Set 1",
        position=1,
        duration_ms=1750000,
        mp3_url="https://phish.in/blob/x.mp3",
        venue_name="McNichols Arena",
        venue_location="Denver, CO",
    )
    dumped = track.model_dump()
    assert dumped["track_id"] == 60001
    rebuilt = Track(**dumped)
    assert rebuilt == track


def test_show_audio_default_tracks_empty_list() -> None:
    audio = ShowAudio(show_id="412", date="1995-12-30")
    assert audio.tracks == []


def test_review_with_optional_fields() -> None:
    review = Review(
        review_id="r-1",
        show_id="123",
        date="1995-12-30",
        author="phan42",
        rating=5.0,
        text="Fire.",
    )
    assert review.posted_at is None


def test_performance_minimal_fields() -> None:
    p = Performance(show_id="1", date="1995-12-30")
    assert p.gap is None


def test_notable_jam_minimal_fields() -> None:
    jam = NotableJam(show_id="1", date="1997-11-17", song_slug="tweezer", song_title="Tweezer")
    assert jam.notes == ""


def test_health_full_envelope() -> None:
    health = Health(
        status="ok",
        stub_mode=True,
        version="0.1.0",
        phishnet=UpstreamHealth(reachable=True, rps_limit=5.0, tokens_available=5.0),
        phishin=UpstreamHealth(reachable=True, rps_limit=10.0, tokens_available=10.0),
        cache=CacheHealth(path="/data/x.db", size_bytes=0, ttl_seconds=86400),
    )
    assert health.status == "ok"
    assert health.cache.size_bytes == 0
