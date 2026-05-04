"""Tests for VaultReader against a mocked asyncpg pool.

We don't spin up Postgres here. Instead we wire ``unittest.mock.AsyncMock``
shims onto the connection acquired from a fake pool, and verify the methods:

* call the right SQL (substring assertions, defensive against whitespace),
* pass the right parameters to ``conn.fetch`` / ``conn.fetchrow``,
* return the rows the connection produced (passthrough),
* handle the date-vs-numeric-id branch in ``get_show`` / ``get_audio``.

Tests focus on the contract of ``VaultReader``, not the postgres dialect.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from mcp_phish.vault import VaultReader, _is_date

# ---------------------------------------------------------------------------
# Tiny fake-pool plumbing
# ---------------------------------------------------------------------------


class _FakeConn:
    """A standin for asyncpg.Connection with AsyncMock-backed fetch/fetchrow."""

    def __init__(self) -> None:
        self.fetch = AsyncMock(return_value=[])
        self.fetchrow = AsyncMock(return_value=None)


class _FakePool:
    """Mimics enough of asyncpg.Pool for VaultReader's ``async with`` usage."""

    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> Any:
        @asynccontextmanager
        async def _ctx() -> Any:
            yield self._conn

        return _ctx()


@pytest.fixture
def fake_conn() -> _FakeConn:
    return _FakeConn()


@pytest.fixture
def reader(fake_conn: _FakeConn) -> VaultReader:
    pool = _FakePool(fake_conn)
    return VaultReader(pool)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Helper predicate
# ---------------------------------------------------------------------------


def test_is_date_recognises_iso_only() -> None:
    assert _is_date("1995-12-30") is True
    assert _is_date("99-12-30") is False
    assert _is_date("1995/12/30") is False
    assert _is_date("12345") is False
    assert _is_date("") is False


# ---------------------------------------------------------------------------
# get_show
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_show_by_date_executes_date_branch(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    fake_show = {"date": "1995-12-30", "show_id_phishin": 412412, "show_id_phishnet": 1253}
    fake_setlist = [{"set_label": "1", "position": 1, "song_slug": "reba", "song_name": "Reba"}]
    fake_conn.fetchrow.return_value = fake_show
    fake_conn.fetch.return_value = fake_setlist

    show, setlist = await reader.get_show("1995-12-30")

    assert show is fake_show
    assert setlist == fake_setlist
    # First fetchrow ran the date branch (parameterized as $1::date).
    assert fake_conn.fetchrow.await_args is not None
    sql_text = fake_conn.fetchrow.await_args.args[0]
    assert "s.date = $1::date" in sql_text
    assert fake_conn.fetchrow.await_args.args[1] == "1995-12-30"
    # Setlist lookup runs against setlist_notes for the same date.
    assert fake_conn.fetch.await_args is not None
    setlist_sql = fake_conn.fetch.await_args.args[0]
    assert "FROM   setlist_notes sn" in setlist_sql


@pytest.mark.asyncio
async def test_get_show_by_id_executes_id_branch(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    fake_conn.fetchrow.return_value = {
        "date": "1997-11-17",
        "show_id_phishin": 12345,
        "show_id_phishnet": 678,
    }
    fake_conn.fetch.return_value = []

    show, _ = await reader.get_show("12345")

    assert show is not None
    sql_text = fake_conn.fetchrow.await_args.args[0]
    assert "s.show_id_phishin = $1" in sql_text
    # The numeric value is coerced to int for the integer column.
    assert fake_conn.fetchrow.await_args.args[1] == 12345


@pytest.mark.asyncio
async def test_get_show_returns_none_for_unparseable_token(reader: VaultReader) -> None:
    show, setlist = await reader.get_show("not-a-date-or-id")
    assert show is None
    assert setlist == []


@pytest.mark.asyncio
async def test_get_show_missing_returns_empty_setlist(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    fake_conn.fetchrow.return_value = None
    show, setlist = await reader.get_show("1900-01-01")
    assert show is None
    assert setlist == []
    # We should NOT have queried setlist_notes when the show is missing.
    fake_conn.fetch.assert_not_awaited()


# ---------------------------------------------------------------------------
# search_shows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_shows_no_filters_uses_only_limit(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    fake_conn.fetch.return_value = [{"date": "1995-12-30"}]
    rows = await reader.search_shows(limit=5)
    assert rows
    sql_text, *args = fake_conn.fetch.await_args.args
    assert "WHERE" not in sql_text  # no clauses
    assert args == [5]


@pytest.mark.asyncio
async def test_search_shows_combines_year_and_geo(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    fake_conn.fetch.return_value = []
    await reader.search_shows(year=1997, venue="MSG", state="NY", limit=10)
    sql_text, *args = fake_conn.fetch.await_args.args
    assert "EXTRACT(YEAR FROM s.date)" in sql_text
    assert "v.name ILIKE" in sql_text
    assert "v.state ILIKE" in sql_text
    # Year first, then venue, then state, then limit.
    assert args == [1997, "%MSG%", "%NY%", 10]


# ---------------------------------------------------------------------------
# recent_shows / get_song / search_songs / song_history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_shows_orders_desc_with_limit(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    fake_conn.fetch.return_value = []
    await reader.recent_shows(limit=7)
    sql_text, limit_arg = fake_conn.fetch.await_args.args
    assert "ORDER  BY s.date DESC" in sql_text
    assert limit_arg == 7


@pytest.mark.asyncio
async def test_get_song_runs_slug_lookup(reader: VaultReader, fake_conn: _FakeConn) -> None:
    fake_conn.fetchrow.return_value = {"slug": "ghost", "title": "Ghost"}
    row = await reader.get_song("ghost")
    assert row == {"slug": "ghost", "title": "Ghost"}
    sql_text, slug_arg = fake_conn.fetchrow.await_args.args
    assert "WHERE  slug = $1" in sql_text
    assert slug_arg == "ghost"


@pytest.mark.asyncio
async def test_search_songs_uses_ilike_with_query(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    fake_conn.fetch.return_value = []
    await reader.search_songs("fluff", limit=5)
    sql_text, query_arg, limit_arg = fake_conn.fetch.await_args.args
    assert "s.title ILIKE $1" in sql_text
    assert query_arg == "%fluff%"
    assert limit_arg == 5


@pytest.mark.asyncio
async def test_search_songs_joins_song_aliases_local(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    """The new alias-aware SQL must LEFT JOIN song_aliases_local."""
    fake_conn.fetch.return_value = []
    await reader.search_songs("yem", limit=5)
    sql_text, _query_arg, _limit_arg = fake_conn.fetch.await_args.args
    assert "song_aliases_local" in sql_text
    assert "LEFT JOIN" in sql_text
    assert "a.alias ILIKE $1" in sql_text
    # DISTINCT prevents row duplication when a song has multiple aliases.
    assert "SELECT DISTINCT" in sql_text


@pytest.mark.asyncio
async def test_validate_slugs_returns_intersection(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    """validate_slugs runs one SELECT and returns matching slugs as a set."""
    fake_conn.fetch.return_value = [
        {"slug": "fluffhead"},
        {"slug": "tweezer"},
    ]
    result = await reader.validate_slugs(["fluffhead", "tweezer", "blarghhh"])
    assert result == {"fluffhead", "tweezer"}
    sql_text, slugs_arg = fake_conn.fetch.await_args.args
    assert "slug = ANY($1::text[])" in sql_text
    assert slugs_arg == ["fluffhead", "tweezer", "blarghhh"]


@pytest.mark.asyncio
async def test_validate_slugs_empty_input_short_circuits(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    """An empty input list must NOT issue a SQL call."""
    result = await reader.validate_slugs([])
    assert result == set()
    fake_conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_song_history_joins_tracks_and_shows(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    fake_conn.fetch.return_value = []
    await reader.song_history("ghost", limit=20)
    sql_text, slug_arg, limit_arg = fake_conn.fetch.await_args.args
    assert "FROM   track_songs ts" in sql_text
    assert "JOIN   tracks tr" in sql_text
    assert "JOIN   shows  s" in sql_text
    assert slug_arg == "ghost"
    assert limit_arg == 20


# ---------------------------------------------------------------------------
# jam_chart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jam_chart_no_year_passes_only_limit(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    fake_conn.fetch.return_value = []
    await reader.jam_chart(limit=15)
    sql_text, *args = fake_conn.fetch.await_args.args
    assert "EXTRACT(YEAR FROM jc.show_date)" not in sql_text
    assert args == [15]


@pytest.mark.asyncio
async def test_jam_chart_with_year_adds_filter(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    fake_conn.fetch.return_value = []
    await reader.jam_chart(year=1997, limit=5)
    sql_text, *args = fake_conn.fetch.await_args.args
    assert "EXTRACT(YEAR FROM jc.show_date) = $1" in sql_text
    assert args == [1997, 5]


# ---------------------------------------------------------------------------
# get_reviews
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_reviews_filters_by_date(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    fake_conn.fetch.return_value = []
    await reader.get_reviews("1995-12-30", limit=3)
    sql_text, date_arg, limit_arg = fake_conn.fetch.await_args.args
    assert "WHERE  show_date = $1::date" in sql_text
    assert date_arg == "1995-12-30"
    assert limit_arg == 3


# ---------------------------------------------------------------------------
# get_audio + get_track + search_audio_tracks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_audio_by_date_pulls_tracks(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    fake_conn.fetchrow.return_value = {"date": "1997-11-17"}
    fake_conn.fetch.return_value = [{"id": 1, "show_date": "1997-11-17"}]

    show, tracks = await reader.get_audio("1997-11-17")

    assert show == {"date": "1997-11-17"}
    assert tracks == [{"id": 1, "show_date": "1997-11-17"}]
    show_sql = fake_conn.fetchrow.await_args.args[0]
    assert "s.date = $1::date" in show_sql
    tracks_sql = fake_conn.fetch.await_args.args[0]
    assert "FROM   tracks" in tracks_sql


@pytest.mark.asyncio
async def test_get_audio_by_id_uses_id_branch(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    fake_conn.fetchrow.return_value = {"date": "1997-11-17"}
    fake_conn.fetch.return_value = []
    await reader.get_audio("999")
    show_sql, id_arg = fake_conn.fetchrow.await_args.args
    assert "s.show_id_phishin = $1" in show_sql
    assert id_arg == 999


@pytest.mark.asyncio
async def test_get_audio_unparseable_returns_empty(reader: VaultReader) -> None:
    show, tracks = await reader.get_audio("garbage")
    assert show is None
    assert tracks == []


@pytest.mark.asyncio
async def test_get_track_filters_by_id(reader: VaultReader, fake_conn: _FakeConn) -> None:
    fake_conn.fetchrow.return_value = {"id": 60001, "slug": "tweezer"}
    row = await reader.get_track(60001)
    assert row is not None
    sql_text, id_arg = fake_conn.fetchrow.await_args.args
    assert "WHERE  tr.id = $1" in sql_text
    assert id_arg == 60001


@pytest.mark.asyncio
async def test_search_audio_tracks_filters_by_slug(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    fake_conn.fetch.return_value = []
    await reader.search_audio_tracks("tweezer", limit=4)
    sql_text, slug_arg, limit_arg = fake_conn.fetch.await_args.args
    assert "WHERE  tr.slug = $1" in sql_text
    assert slug_arg == "tweezer"
    assert limit_arg == 4


# ---------------------------------------------------------------------------
# Analytical tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_venue_history_orders_desc(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    fake_conn.fetch.return_value = []
    await reader.venue_history("madison-square-garden", limit=10)
    sql_text, slug_arg, limit_arg = fake_conn.fetch.await_args.args
    assert "WHERE  s.venue_slug = $1" in sql_text
    assert "ORDER  BY s.date DESC" in sql_text
    assert slug_arg == "madison-square-garden"
    assert limit_arg == 10


@pytest.mark.asyncio
async def test_songs_by_gap_skips_null_gap(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    fake_conn.fetch.return_value = []
    await reader.songs_by_gap(limit=8)
    sql_text, limit_arg = fake_conn.fetch.await_args.args
    assert "WHERE  gap_current IS NOT NULL" in sql_text
    assert "ORDER  BY gap_current DESC" in sql_text
    assert limit_arg == 8


# ---------------------------------------------------------------------------
# ETL health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_last_etl_run_returns_dict(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    finished = datetime(2026, 5, 3, 6, 0, 0, tzinfo=UTC)
    fake_conn.fetchrow.return_value = {
        "id": 42,
        "started_at": finished,
        "finished_at": finished,
        "mode": "incremental",
        "status": "ok",
        "rows_added": 12,
        "rows_updated": 7,
    }
    out = await reader.last_etl_run()
    assert out is not None
    assert out["status"] == "ok"
    assert out["finished_at"] is finished


@pytest.mark.asyncio
async def test_last_etl_run_none_when_table_empty(
    reader: VaultReader, fake_conn: _FakeConn
) -> None:
    fake_conn.fetchrow.return_value = None
    assert await reader.last_etl_run() is None
