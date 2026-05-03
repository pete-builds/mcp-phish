"""End-to-end tool tests against the FastMCP server.

We invoke each tool's underlying coroutine directly via the FastMCP tool
manager. This validates wire-format JSON, projection logic, error handling,
and cache integration without spinning up the HTTP transport.
"""

from __future__ import annotations

import json
from typing import Any

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
    Song,
    SongSummary,
    Track,
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
