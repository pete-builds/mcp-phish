"""Tests for the in-memory stub clients."""

from __future__ import annotations

import pytest

from mcp_phish.clients.phishin import PhishInError
from mcp_phish.clients.phishnet import PhishNetError
from mcp_phish.clients.stubs import StubPhishInClient, StubPhishNetClient


@pytest.mark.asyncio
async def test_phishnet_get_show_by_date_known() -> None:
    client = StubPhishNetClient()
    rows = await client.get_show_by_date("1995-12-30")
    assert isinstance(rows, list) and rows
    assert rows[0]["venue"] == "Madison Square Garden"


@pytest.mark.asyncio
async def test_phishnet_get_show_by_date_unknown_raises() -> None:
    client = StubPhishNetClient()
    with pytest.raises(PhishNetError):
        await client.get_show_by_date("1900-01-01")


@pytest.mark.asyncio
async def test_phishnet_search_filters_year_state() -> None:
    client = StubPhishNetClient()
    rows = await client.search_shows({"year": 1995, "state": "NY"})
    assert all(r["showdate"].startswith("1995") and r["state"] == "NY" for r in rows)


@pytest.mark.asyncio
async def test_phishnet_song_lookup_and_search() -> None:
    client = StubPhishNetClient()
    by_slug = await client.get_song_by_slug("fluffhead")
    assert by_slug[0]["title"] == "Fluffhead"
    matches = await client.search_songs({"query": "fluff"})
    assert any(s["slug"] == "fluffhead" for s in matches)


@pytest.mark.asyncio
async def test_phishnet_setlist_includes_showid() -> None:
    client = StubPhishNetClient()
    rows = await client.get_setlist_by_date("1995-12-30")
    assert all("showid" in row for row in rows)
    assert any(row["song"] == "Mike's Song" for row in rows)


@pytest.mark.asyncio
async def test_phishnet_jam_chart_year_filter() -> None:
    client = StubPhishNetClient()
    rows = await client.jam_chart({"year": 1997})
    assert all(r["showdate"].startswith("1997") for r in rows)


@pytest.mark.asyncio
async def test_phishnet_reviews_by_id() -> None:
    client = StubPhishNetClient()
    rows = await client.reviews_by_id("1252691618")
    assert rows
    assert rows[0]["showdate"] == "1995-12-30"


@pytest.mark.asyncio
async def test_phishin_get_show_by_date_and_id() -> None:
    client = StubPhishInClient()
    by_date = await client.get_show("1997-11-17")
    by_id = await client.get_show("412412")
    assert by_date["id"] == 412412
    assert by_id["date"] == "1997-11-17"


@pytest.mark.asyncio
async def test_phishin_get_show_unknown_raises() -> None:
    client = StubPhishInClient()
    with pytest.raises(PhishInError):
        await client.get_show("0000-00-00")


@pytest.mark.asyncio
async def test_phishin_get_track() -> None:
    client = StubPhishInClient()
    track = await client.get_track(60001)
    assert track["slug"] == "tweezer"


@pytest.mark.asyncio
async def test_phishin_search_tracks_envelope() -> None:
    client = StubPhishInClient()
    payload = await client.search_tracks({"slug": "tweezer", "per_page": 5})
    assert "tracks" in payload and isinstance(payload["tracks"], list)
    assert payload["total_entries"] == len(payload["tracks"])
