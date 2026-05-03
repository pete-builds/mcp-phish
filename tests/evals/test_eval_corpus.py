"""Evaluation corpus: fixed natural-language phan questions → expected tool calls.

Each entry asserts that:

1. The right tool is the obvious answer to the question.
2. Calling that tool with the obvious args returns the expected anchor data
   (specific show, song, or audio asset).

Run via ``pytest -k eval``. The eval suite is small on purpose — it is a
contract guard, not a coverage tool. The full coverage suite lives in the
sibling files.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mcp_phish.cache import ResponseCache
from mcp_phish.config import Settings
from mcp_phish.models import Show, ShowAudio, Song
from mcp_phish.server import build_server
from mcp_phish.throttle import TokenBucket

pytestmark = pytest.mark.eval


async def _call(server: Any, tool: str, **kwargs: Any) -> dict[str, Any]:
    """Invoke a FastMCP-registered tool by name and return its parsed JSON."""
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
# Q1: "What was the setlist on 12/30/95?"
#   → get_show("1995-12-30") → Madison Square Garden
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eval_setlist_19951230_msg(server: Any) -> None:
    body = await _call(server, "get_show", date_or_id="1995-12-30")
    show = Show(**body["data"])
    assert show.venue.name == "Madison Square Garden"
    assert show.date == "1995-12-30"
    slugs = [entry.song_slug for entry in show.setlist]
    assert "mikes-song" in slugs
    assert "fluffhead" in slugs  # encore


# ---------------------------------------------------------------------------
# Q2: "How many times has Fluffhead been played?"
#   → get_song("fluffhead") → integer total
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eval_fluffhead_song_info(server: Any) -> None:
    body = await _call(server, "get_song", slug="fluffhead")
    song = Song(**body["data"])
    assert song.title == "Fluffhead"
    assert song.times_played > 0
    assert song.debut_date == "1986-04-15"


# ---------------------------------------------------------------------------
# Q3: "Give me the audio for 11/17/97."
#   → get_audio("1997-11-17") → McNichols Arena, Tweezer + Ghost tracks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eval_audio_19971117_denver(server: Any) -> None:
    body = await _call(server, "get_audio", show_id_or_date="1997-11-17")
    audio = ShowAudio(**body["data"])
    assert audio.date == "1997-11-17"
    assert "Denver" in audio.venue_location
    track_slugs = {t.slug for t in audio.tracks}
    assert {"tweezer", "ghost"}.issubset(track_slugs)


# ---------------------------------------------------------------------------
# Q4: "Search for songs with 'mike' in the title."
#   → search_songs("mike") → mikes-song in results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eval_search_songs_mike(server: Any) -> None:
    body = await _call(server, "search_songs", query="mike", limit=10)
    slugs = [row["slug"] for row in body["data"]]
    assert "mikes-song" in slugs


# ---------------------------------------------------------------------------
# Q5: "Show me jam-chart entries from 1997."
#   → jam_chart(year=1997) → Tweezer (Denver) ranks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eval_jam_chart_1997(server: Any) -> None:
    body = await _call(server, "jam_chart", year=1997, limit=10)
    rows = body["data"]
    assert rows
    assert any(r["song_slug"] == "tweezer" for r in rows)
    assert all(r["date"].startswith("1997") for r in rows)
