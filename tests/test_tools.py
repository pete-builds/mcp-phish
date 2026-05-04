"""End-to-end tool tests against the FastMCP server.

We invoke each tool's underlying coroutine directly via the FastMCP tool
manager. This validates wire-format JSON, projection logic, error handling,
and cache integration without spinning up the HTTP transport.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_phish.cache import ResponseCache
from mcp_phish.config import Settings
from mcp_phish.models import (
    Health,
    NotableJam,
    Performance,
    Review,
    Show,
    ShowAudio,
    ShowSummary,
    SlugValidation,
    Song,
    SongGap,
    SongSummary,
    Track,
    VenueShow,
)
from mcp_phish.server import build_server
from mcp_phish.throttle import TokenBucket

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _call(server: Any, tool: str, **kwargs: Any) -> dict[str, Any]:
    """Invoke a FastMCP-registered tool by name and return its parsed JSON.

    FastMCP's ``call_tool`` returns a :class:`ToolResult` whose ``content[0]``
    is a ``TextContent`` carrying the tool's stringified return value.
    """
    result = await server.call_tool(tool, kwargs)
    if hasattr(result, "content") and result.content:
        raw = getattr(result.content[0], "text", "") or ""
    elif hasattr(result, "structured_content") and result.structured_content is not None:
        sc = result.structured_content
        if isinstance(sc, dict) and "result" in sc and isinstance(sc["result"], str):
            raw = sc["result"]
        else:
            raw = json.dumps(sc)
    else:
        raw = str(result)
    return json.loads(raw)


@pytest.fixture
def server(stub_settings: Settings) -> Any:
    cache = ResponseCache(
        db_path=stub_settings.cache_db_path,
        ttl_seconds=stub_settings.cache_ttl_seconds,
    )
    return build_server(
        stub_settings,
        cache=cache,
        phishnet_throttle=TokenBucket(rps=100),
        phishin_throttle=TokenBucket(rps=100),
    )


# ---------------------------------------------------------------------------
# phish.net tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_show_returns_validated_show(server: Any) -> None:
    body = await _call(server, "get_show", date_or_id="1995-12-30")
    assert "data" in body
    show = Show(**body["data"])
    assert show.date == "1995-12-30"
    assert show.venue.name == "Madison Square Garden"
    # Setlist contains Mike's > Simple > Weekapaug from the stub fixtures.
    slugs = [entry.song_slug for entry in show.setlist]
    assert {"mikes-song", "simple", "weekapaug-groove"}.issubset(slugs)


@pytest.mark.asyncio
async def test_get_show_invalid_input_returns_error(server: Any) -> None:
    body = await _call(server, "get_show", date_or_id="")
    assert body["code"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_get_show_not_found(server: Any) -> None:
    body = await _call(server, "get_show", date_or_id="1900-01-01")
    assert body["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_search_shows_year_filter(server: Any) -> None:
    body = await _call(server, "search_shows", year=1995, limit=5)
    summaries = [ShowSummary(**row) for row in body["data"]]
    assert summaries
    assert all(s.date.startswith("1995") for s in summaries)


@pytest.mark.asyncio
async def test_recent_shows_returns_sorted_summaries(server: Any) -> None:
    body = await _call(server, "recent_shows", limit=10)
    summaries = [ShowSummary(**row) for row in body["data"]]
    dates = [s.date for s in summaries]
    assert dates == sorted(dates, reverse=True)


@pytest.mark.asyncio
async def test_search_songs_query_required(server: Any) -> None:
    body = await _call(server, "search_songs", query="")
    assert body["code"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_search_songs_returns_summaries(server: Any) -> None:
    body = await _call(server, "search_songs", query="fluff", limit=5)
    summaries = [SongSummary(**row) for row in body["data"]]
    assert any(s.slug == "fluffhead" for s in summaries)


@pytest.mark.asyncio
async def test_get_song_full_record(server: Any) -> None:
    body = await _call(server, "get_song", slug="fluffhead")
    song = Song(**body["data"])
    assert song.slug == "fluffhead"
    assert song.times_played == 264
    assert song.debut_date == "1986-04-15"


@pytest.mark.asyncio
async def test_get_song_invalid_input(server: Any) -> None:
    body = await _call(server, "get_song", slug="")
    assert body["code"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_get_song_not_found(server: Any) -> None:
    body = await _call(server, "get_song", slug="not-a-real-song-slug")
    assert body["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_song_history(server: Any) -> None:
    body = await _call(server, "song_history", slug="fluffhead", limit=10)
    perfs = [Performance(**row) for row in body["data"]]
    assert perfs
    # Most-recent first.
    assert perfs[0].date >= perfs[-1].date


@pytest.mark.asyncio
async def test_jam_chart_year_filter(server: Any) -> None:
    body = await _call(server, "jam_chart", year=1997, limit=10)
    jams = [NotableJam(**row) for row in body["data"]]
    assert jams
    assert all(j.date.startswith("1997") for j in jams)


@pytest.mark.asyncio
async def test_get_reviews_by_date(server: Any) -> None:
    body = await _call(server, "get_reviews", show_id_or_date="1995-12-30", limit=5)
    reviews = [Review(**row) for row in body["data"]]
    assert reviews
    assert all(r.date == "1995-12-30" for r in reviews)


@pytest.mark.asyncio
async def test_get_reviews_invalid_input(server: Any) -> None:
    body = await _call(server, "get_reviews", show_id_or_date="")
    assert body["code"] == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# phish.in tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_audio_returns_show_audio(server: Any) -> None:
    body = await _call(server, "get_audio", show_id_or_date="1997-11-17")
    audio = ShowAudio(**body["data"])
    assert audio.date == "1997-11-17"
    assert audio.tracks
    assert {"tweezer", "ghost"}.issubset({t.slug for t in audio.tracks})


@pytest.mark.asyncio
async def test_get_audio_invalid_input(server: Any) -> None:
    body = await _call(server, "get_audio", show_id_or_date="")
    assert body["code"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_get_audio_not_found(server: Any) -> None:
    body = await _call(server, "get_audio", show_id_or_date="1900-01-01")
    assert body["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_get_track(server: Any) -> None:
    body = await _call(server, "get_track", track_id=60001)
    track = Track(**body["data"])
    assert track.slug == "tweezer"


@pytest.mark.asyncio
async def test_get_track_invalid(server: Any) -> None:
    body = await _call(server, "get_track", track_id=0)
    assert body["code"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_search_audio_tracks(server: Any) -> None:
    body = await _call(server, "search_audio_tracks", song_slug="tweezer", limit=5)
    tracks = [Track(**row) for row in body["data"]]
    assert tracks
    assert all(t.slug == "tweezer" for t in tracks)


@pytest.mark.asyncio
async def test_search_audio_tracks_invalid(server: Any) -> None:
    body = await _call(server, "search_audio_tracks", song_slug="")
    assert body["code"] == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_reports_stub_and_throttle(server: Any) -> None:
    body = await _call(server, "health")
    health = Health(**body["data"])
    assert health.stub_mode is True
    assert health.phishnet.rps_limit > 0
    assert health.phishin.rps_limit > 0
    assert health.cache.ttl_seconds == 86400


# ---------------------------------------------------------------------------
# cache integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_call_is_cache_hit(stub_settings: Settings) -> None:
    cache = ResponseCache(
        db_path=stub_settings.cache_db_path,
        ttl_seconds=stub_settings.cache_ttl_seconds,
    )
    server = build_server(
        stub_settings,
        cache=cache,
        phishnet_throttle=TokenBucket(rps=100),
        phishin_throttle=TokenBucket(rps=100),
    )
    # First call: miss + put.
    await _call(server, "get_song", slug="fluffhead")
    size_after_first = cache.size_bytes()
    last_miss_ts = cache.last_miss_ts
    assert size_after_first > 0
    assert last_miss_ts is not None

    # Second call with same args: hit. last_hit_ts populates.
    await _call(server, "get_song", slug="fluffhead")
    assert cache.last_hit_ts is not None
    # File size shouldn't grow (only one row exists).
    assert cache.size_bytes() == size_after_first


@pytest.mark.asyncio
async def test_cache_distinguishes_args(stub_settings: Settings) -> None:
    """Each unique (endpoint, params) pair produces a separate cache row."""
    cache = ResponseCache(
        db_path=stub_settings.cache_db_path,
        ttl_seconds=stub_settings.cache_ttl_seconds,
    )
    server = build_server(
        stub_settings,
        cache=cache,
        phishnet_throttle=TokenBucket(rps=100),
        phishin_throttle=TokenBucket(rps=100),
    )
    # Different slugs -> different params_hash -> distinct rows.
    await _call(server, "get_song", slug="fluffhead")
    await _call(server, "get_song", slug="tweezer")
    # Inspect the underlying DB directly so we don't depend on page-size growth.
    import aiosqlite

    async with (
        aiosqlite.connect(cache.db_path) as db,
        db.execute("SELECT COUNT(*) FROM cache WHERE endpoint = 'phishnet:get_song'") as cur,
    ):
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == 2


# ---------------------------------------------------------------------------
# Vault-path routing
#
# These tests pass a stub VaultReader into ``build_server`` and confirm that
# vault-eligible tools route through the vault projections instead of the
# live API. The point is the dispatcher logic, not the SQL — VaultReader
# itself is exercised in ``test_vault.py``.
# ---------------------------------------------------------------------------


def _vault_settings(stub_settings: Settings) -> Settings:
    """Clone stub_settings with vault_enabled=True and tighter hot window."""
    return stub_settings.model_copy(
        update={
            "vault_enabled": True,
            "vault_hot_window_hours": 1,  # so a 1995 date never reads live
        }
    )


def _make_vault_stub(**overrides: Any) -> AsyncMock:
    """Build an AsyncMock with default empty returns for VaultReader methods."""
    stub = AsyncMock()
    stub.get_show.return_value = (None, [])
    stub.search_shows.return_value = []
    stub.recent_shows.return_value = []
    stub.get_song.return_value = None
    stub.search_songs.return_value = []
    stub.song_history.return_value = []
    stub.jam_chart.return_value = []
    stub.get_reviews.return_value = []
    stub.get_audio.return_value = (None, [])
    stub.get_track.return_value = None
    stub.search_audio_tracks.return_value = []
    stub.venue_history.return_value = []
    stub.songs_by_gap.return_value = []
    stub.validate_slugs.return_value = set()
    stub.last_etl_run.return_value = None
    for name, value in overrides.items():
        getattr(stub, name).return_value = value
    return stub


def _build(stub_settings: Settings, **kwargs: Any) -> Any:
    cache = ResponseCache(
        db_path=stub_settings.cache_db_path,
        ttl_seconds=stub_settings.cache_ttl_seconds,
    )
    return build_server(
        stub_settings,
        cache=cache,
        phishnet_throttle=TokenBucket(rps=100),
        phishin_throttle=TokenBucket(rps=100),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_vault_get_show_returns_projection_from_record(stub_settings: Settings) -> None:
    show_row = {
        "date": "1995-12-30",
        "show_id_phishin": 412412,
        "show_id_phishnet": 1253,
        "venue_slug": "madison-square-garden",
        "venue_name": "Madison Square Garden",
        "city": "New York",
        "state": "NY",
        "country": "USA",
        "location": "New York, NY",
        "tour_name": "1995 NYE Run",
    }
    setlist_rows = [
        {
            "set_label": "1",
            "position": 1,
            "song_slug": "reba",
            "song_name": "Reba",
            "transition": ">",
            "footnote": "",
        }
    ]
    vault = _make_vault_stub(get_show=(show_row, setlist_rows))
    server = _build(_vault_settings(stub_settings), vault_reader=vault)

    body = await _call(server, "get_show", date_or_id="1995-12-30")
    show = Show(**body["data"])
    assert show.show_id == "412412"
    assert show.venue.name == "Madison Square Garden"
    assert show.venue.slug == "madison-square-garden"
    assert show.setlist[0].song_slug == "reba"
    vault.get_show.assert_awaited_once_with("1995-12-30")


@pytest.mark.asyncio
async def test_vault_get_show_not_found_returns_error(stub_settings: Settings) -> None:
    vault = _make_vault_stub()  # default get_show => (None, [])
    server = _build(_vault_settings(stub_settings), vault_reader=vault)
    body = await _call(server, "get_show", date_or_id="1900-01-01")
    assert body["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_vault_search_shows_uses_vault_projection(stub_settings: Settings) -> None:
    rows = [
        {
            "date": "1997-11-17",
            "show_id_phishin": 12345,
            "show_id_phishnet": 678,
            "venue_name": "McNichols Arena",
            "location": "Denver, CO",
            "tour_name": "Fall 1997",
        }
    ]
    vault = _make_vault_stub(search_shows=rows)
    server = _build(_vault_settings(stub_settings), vault_reader=vault)
    body = await _call(server, "search_shows", year=1997, limit=5)
    summaries = [ShowSummary(**row) for row in body["data"]]
    assert summaries
    assert summaries[0].venue_name == "McNichols Arena"
    vault.search_shows.assert_awaited_once()


@pytest.mark.asyncio
async def test_vault_get_song_returns_projection(stub_settings: Settings) -> None:
    vault = _make_vault_stub(
        get_song={
            "slug": "ghost",
            "title": "Ghost",
            "artist": None,
            "original": True,
            "times_played": 312,
            "debut_date": "1997-06-13",
            "last_play_date": "2024-12-31",
            "gap_current": 0,
        }
    )
    server = _build(_vault_settings(stub_settings), vault_reader=vault)
    body = await _call(server, "get_song", slug="ghost")
    song = Song(**body["data"])
    assert song.slug == "ghost"
    assert song.gap == 0
    assert song.last_played_date == "2024-12-31"


@pytest.mark.asyncio
async def test_vault_venue_history_tool(stub_settings: Settings) -> None:
    rows = [
        {
            "date": "2024-12-31",
            "show_id_phishin": 99999,
            "show_id_phishnet": 1,
            "venue_name": "Madison Square Garden",
            "location": "New York, NY",
            "tour_name": "2024 NYE Run",
        }
    ]
    vault = _make_vault_stub(venue_history=rows)
    server = _build(_vault_settings(stub_settings), vault_reader=vault)
    body = await _call(server, "venue_history", venue_slug="madison-square-garden", limit=10)
    venues = [VenueShow(**row) for row in body["data"]]
    assert venues
    assert venues[0].show_id == "99999"


@pytest.mark.asyncio
async def test_venue_history_requires_vault(stub_settings: Settings) -> None:
    """Without vault enabled (and no reader), tool returns VAULT_DISABLED."""
    server = _build(stub_settings)  # vault_enabled defaults to False
    body = await _call(server, "venue_history", venue_slug="msg", limit=5)
    assert body["code"] == "VAULT_DISABLED"


@pytest.mark.asyncio
async def test_vault_songs_by_gap_tool(stub_settings: Settings) -> None:
    rows = [
        {
            "slug": "harpua",
            "title": "Harpua",
            "times_played": 50,
            "gap_current": 200,
            "last_play_date": "2019-08-30",
        }
    ]
    vault = _make_vault_stub(songs_by_gap=rows)
    server = _build(_vault_settings(stub_settings), vault_reader=vault)
    body = await _call(server, "songs_by_gap", limit=10)
    gaps = [SongGap(**row) for row in body["data"]]
    assert gaps[0].slug == "harpua"
    assert gaps[0].gap_current == 200


@pytest.mark.asyncio
async def test_songs_by_gap_requires_vault(stub_settings: Settings) -> None:
    server = _build(stub_settings)
    body = await _call(server, "songs_by_gap", limit=5)
    assert body["code"] == "VAULT_DISABLED"


@pytest.mark.asyncio
async def test_vault_failure_falls_back_to_live(stub_settings: Settings) -> None:
    """If vault throws, the server falls back to the live (stub) path."""
    vault = _make_vault_stub()
    vault.get_song.side_effect = RuntimeError("simulated vault outage")
    server = _build(_vault_settings(stub_settings), vault_reader=vault)
    body = await _call(server, "get_song", slug="fluffhead")
    # Stub fixtures contain "fluffhead" — fallback succeeds.
    song = Song(**body["data"])
    assert song.slug == "fluffhead"


@pytest.mark.asyncio
async def test_health_reports_vault_disabled_by_default(stub_settings: Settings) -> None:
    server = _build(stub_settings)
    body = await _call(server, "health")
    health = Health(**body["data"])
    assert health.vault.enabled is False


@pytest.mark.asyncio
async def test_health_reports_vault_enabled_with_etl(stub_settings: Settings) -> None:
    from datetime import UTC, datetime, timedelta

    finished = datetime.now(tz=UTC) - timedelta(hours=2)
    vault = _make_vault_stub(
        last_etl_run={
            "id": 1,
            "started_at": finished,
            "finished_at": finished,
            "mode": "incremental",
            "status": "ok",
            "rows_added": 5,
            "rows_updated": 2,
        }
    )
    server = _build(_vault_settings(stub_settings), vault_reader=vault)
    body = await _call(server, "health")
    health = Health(**body["data"])
    assert health.vault.enabled is True
    assert health.vault.stale is False
    assert health.vault.staleness_hours is not None
    assert 1 < health.vault.staleness_hours < 3


@pytest.mark.asyncio
async def test_health_marks_vault_stale_when_etl_too_old(stub_settings: Settings) -> None:
    from datetime import UTC, datetime, timedelta

    finished = datetime.now(tz=UTC) - timedelta(hours=72)  # > default 36h max
    vault = _make_vault_stub(
        last_etl_run={
            "id": 1,
            "started_at": finished,
            "finished_at": finished,
            "mode": "incremental",
            "status": "ok",
            "rows_added": 0,
            "rows_updated": 0,
        }
    )
    server = _build(_vault_settings(stub_settings), vault_reader=vault)
    body = await _call(server, "health")
    health = Health(**body["data"])
    assert health.status == "degraded"
    assert health.vault.stale is True


# ---------------------------------------------------------------------------
# validate_song_slugs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_song_slugs_empty_list_invalid(server: Any) -> None:
    body = await _call(server, "validate_song_slugs", slugs=[])
    assert body["code"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_validate_song_slugs_oversize_invalid(server: Any) -> None:
    too_many = [f"song-{i}" for i in range(51)]
    body = await _call(server, "validate_song_slugs", slugs=too_many)
    assert body["code"] == "INVALID_INPUT"
    assert body.get("details", {}).get("count") == 51


@pytest.mark.asyncio
async def test_validate_song_slugs_rejects_empty_strings(server: Any) -> None:
    body = await _call(server, "validate_song_slugs", slugs=["fluffhead", ""])
    assert body["code"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_validate_song_slugs_live_fallback_partitions(server: Any) -> None:
    """Without vault, the tool fans out to phish.net (stub) per slug.

    Stub catalog contains: fluffhead, tweezer, mikes-song, ghost, reba,
    satisfaction. Anything else is unknown.
    """
    body = await _call(
        server,
        "validate_song_slugs",
        slugs=["tweezer", "blarghhh", "fluffhead", "totallyfakething"],
    )
    sv = SlugValidation(**body["data"])
    assert set(sv.valid) == {"tweezer", "fluffhead"}
    # valid is sorted for determinism
    assert sv.valid == sorted(sv.valid)
    # unknown preserves request order
    assert sv.unknown == ["blarghhh", "totallyfakething"]


@pytest.mark.asyncio
async def test_validate_song_slugs_uses_vault_when_enabled(stub_settings: Settings) -> None:
    """With vault enabled, the tool calls vault.validate_slugs once."""
    vault = _make_vault_stub(validate_slugs={"tweezer", "fluffhead"})
    server = _build(_vault_settings(stub_settings), vault_reader=vault)
    body = await _call(
        server,
        "validate_song_slugs",
        slugs=["tweezer", "blarghhh", "fluffhead"],
    )
    sv = SlugValidation(**body["data"])
    assert sv.valid == ["fluffhead", "tweezer"]  # sorted
    assert sv.unknown == ["blarghhh"]
    vault.validate_slugs.assert_awaited_once()


@pytest.mark.asyncio
async def test_validate_song_slugs_vault_outage_falls_back(stub_settings: Settings) -> None:
    """If the vault throws, the live (stub) path is used as a safety net."""
    vault = _make_vault_stub()
    vault.validate_slugs.side_effect = RuntimeError("simulated vault outage")
    server = _build(_vault_settings(stub_settings), vault_reader=vault)
    body = await _call(
        server,
        "validate_song_slugs",
        slugs=["fluffhead", "blarghhh"],
    )
    sv = SlugValidation(**body["data"])
    assert sv.valid == ["fluffhead"]
    assert sv.unknown == ["blarghhh"]


@pytest.mark.asyncio
async def test_validate_song_slugs_all_valid(server: Any) -> None:
    body = await _call(
        server,
        "validate_song_slugs",
        slugs=["fluffhead", "tweezer", "ghost"],
    )
    sv = SlugValidation(**body["data"])
    assert sv.valid == ["fluffhead", "ghost", "tweezer"]
    assert sv.unknown == []


@pytest.mark.asyncio
async def test_validate_song_slugs_all_unknown(server: Any) -> None:
    body = await _call(
        server,
        "validate_song_slugs",
        slugs=["nope-1", "nope-2"],
    )
    sv = SlugValidation(**body["data"])
    assert sv.valid == []
    assert sv.unknown == ["nope-1", "nope-2"]
