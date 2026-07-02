"""VaultReader — async read layer over the phish-vault Postgres database.

Phase 3 swaps the mcp-phish read path from live API calls to this class.
All methods return asyncpg.Record objects (or tuples of them) so the
projection layer in server.py can map them to the frozen Pydantic models.

The vault schema lives in the phish-vault repo. Key tables used here:
    shows, venues, songs, tracks, track_songs, setlist_notes, reviews,
    jam_chart_entries, tours, etl_runs.

Connection is an asyncpg.Pool injected at construction time. The pool
lifecycle (create / close) is the caller's responsibility.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

logger = logging.getLogger("mcp_phish.vault")


class VaultReader:
    """Async read facade over the phish-vault Postgres database."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ------------------------------------------------------------------
    # Show queries
    # ------------------------------------------------------------------

    async def get_show(self, date_or_id: str) -> tuple[asyncpg.Record | None, list[asyncpg.Record]]:
        """Return (show_row, setlist_rows) for a date (YYYY-MM-DD) or phish.in show id.

        show_row is None when not found. setlist_rows may be empty if the
        vault has the show but lacks a setlist.
        """
        async with self._pool.acquire() as conn:
            if _is_date(date_or_id):
                show_row: asyncpg.Record | None = await conn.fetchrow(
                    """
                    SELECT s.date, s.show_id_phishin, s.show_id_phishnet,
                           s.venue_slug, s.tour_slug, s.duration_ms,
                           s.audio_status, s.cover_art_url_large, s.album_zip_url,
                           v.name  AS venue_name,
                           v.city, v.state, v.country, v.location,
                           v.latitude, v.longitude,
                           t.name  AS tour_name
                    FROM   shows s
                    LEFT JOIN venues v ON v.slug = s.venue_slug
                    LEFT JOIN tours  t ON t.slug = s.tour_slug
                    WHERE  s.date = $1::date
                    """,
                    date_or_id,
                )
            else:
                # Try phish.in id (integer)
                try:
                    show_id_int = int(date_or_id)
                except ValueError:
                    return None, []
                show_row = await conn.fetchrow(
                    """
                    SELECT s.date, s.show_id_phishin, s.show_id_phishnet,
                           s.venue_slug, s.tour_slug, s.duration_ms,
                           s.audio_status, s.cover_art_url_large, s.album_zip_url,
                           v.name  AS venue_name,
                           v.city, v.state, v.country, v.location,
                           v.latitude, v.longitude,
                           t.name  AS tour_name
                    FROM   shows s
                    LEFT JOIN venues v ON v.slug = s.venue_slug
                    LEFT JOIN tours  t ON t.slug = s.tour_slug
                    WHERE  s.show_id_phishin = $1
                    """,
                    show_id_int,
                )

            if show_row is None:
                return None, []

            show_date = str(show_row["date"])
            setlist_rows: list[asyncpg.Record] = await conn.fetch(
                """
                SELECT sn.set_label, sn.position, sn.song_slug,
                       sn.song_name, sn.transition, sn.footnote
                FROM   setlist_notes sn
                WHERE  sn.show_date = $1::date
                ORDER  BY sn.position
                """,
                show_date,
            )

        return show_row, setlist_rows

    async def search_shows(
        self,
        year: int | None = None,
        venue: str = "",
        city: str = "",
        state: str = "",
        country: str = "",
        limit: int = 25,
    ) -> list[asyncpg.Record]:
        """Search shows with optional year + venue/geo filters."""
        clauses: list[str] = []
        args: list[Any] = []
        idx = 1

        if year is not None:
            clauses.append(f"EXTRACT(YEAR FROM s.date) = ${idx}")
            args.append(year)
            idx += 1
        if venue:
            clauses.append(f"v.name ILIKE ${idx}")
            args.append(f"%{venue}%")
            idx += 1
        if city:
            clauses.append(f"v.city ILIKE ${idx}")
            args.append(f"%{city}%")
            idx += 1
        if state:
            clauses.append(f"v.state ILIKE ${idx}")
            args.append(f"%{state}%")
            idx += 1
        if country:
            clauses.append(f"v.country ILIKE ${idx}")
            args.append(f"%{country}%")
            idx += 1

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        args.append(limit)

        # SQL fragments interpolated below (`where`, `idx`) are constructed from
        # internal counters and column-name whitelist clauses, never from user
        # input. All user values flow through asyncpg parameter substitution.
        sql = f"""
            SELECT s.date, s.show_id_phishin, s.show_id_phishnet,
                   v.name AS venue_name, v.location,
                   t.name AS tour_name
            FROM   shows s
            LEFT JOIN venues v ON v.slug = s.venue_slug
            LEFT JOIN tours  t ON t.slug = s.tour_slug
            {where}
            ORDER  BY s.date DESC
            LIMIT  ${idx}
        """  # noqa: S608 — values pass through asyncpg params, fragments are internal
        async with self._pool.acquire() as conn:
            return list(await conn.fetch(sql, *args))

    async def recent_shows(self, limit: int = 10) -> list[asyncpg.Record]:
        """Return the most recent shows, newest first."""
        async with self._pool.acquire() as conn:
            return list(
                await conn.fetch(
                    """
                    SELECT s.date, s.show_id_phishin, s.show_id_phishnet,
                           v.name AS venue_name, v.location,
                           t.name AS tour_name
                    FROM   shows s
                    LEFT JOIN venues v ON v.slug = s.venue_slug
                    LEFT JOIN tours  t ON t.slug = s.tour_slug
                    ORDER  BY s.date DESC
                    LIMIT  $1
                    """,
                    limit,
                )
            )

    # ------------------------------------------------------------------
    # Song queries
    # ------------------------------------------------------------------

    async def get_song(self, slug: str) -> asyncpg.Record | None:
        """Return a single song row by slug, or None."""
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT slug, title, alias, original, artist,
                       tracks_count AS times_played,
                       debut_date, last_play_date, gap_current
                FROM   songs
                WHERE  slug = $1
                """,
                slug,
            )

    async def search_songs(self, query: str, limit: int = 25) -> list[asyncpg.Record]:
        """ILIKE search against song title, upstream alias, and community aliases.

        Joins ``song_aliases_local`` (community-curated nicknames seeded by
        migration 003) so a fan typing "yem" or "rnr" finds the canonical
        song. The LEFT JOIN preserves rows that have no community alias.
        """
        async with self._pool.acquire() as conn:
            return list(
                await conn.fetch(
                    """
                    SELECT DISTINCT s.slug, s.title, s.alias, s.original,
                                    s.artist, s.gap_current,
                                    s.tracks_count AS times_played
                    FROM   songs s
                    LEFT JOIN song_aliases_local a ON a.song_slug = s.slug
                    WHERE  s.title ILIKE $1
                       OR  s.alias ILIKE $1
                       OR  a.alias ILIKE $1
                    ORDER  BY s.tracks_count DESC
                    LIMIT  $2
                    """,
                    f"%{query}%",
                    limit,
                )
            )

    async def validate_slugs(self, slugs: list[str]) -> set[str]:
        """Return the subset of ``slugs`` that exist in ``songs.slug``.

        Single SELECT, single round-trip. Order is not preserved here;
        the caller is responsible for ordering the result against the
        request.
        """
        if not slugs:
            return set()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT slug FROM songs WHERE slug = ANY($1::text[])",
                slugs,
            )
        return {str(row["slug"]) for row in rows}

    async def song_history(self, slug: str, limit: int = 50) -> list[asyncpg.Record]:
        """Return performances of a song, most-recent first.

        Joins tracks + track_songs + shows + venues to produce one row per
        performance with venue and gap context.
        """
        async with self._pool.acquire() as conn:
            return list(
                await conn.fetch(
                    """
                    SELECT tr.show_date AS date,
                           s.show_id_phishin,
                           s.show_id_phishnet,
                           tr.set_name,
                           v.name     AS venue_name,
                           v.location AS venue_location,
                           ts.previous_performance_gap AS gap
                    FROM   track_songs ts
                    JOIN   tracks tr ON tr.id = ts.track_id
                    JOIN   shows  s  ON s.date = tr.show_date
                    LEFT JOIN venues v ON v.slug = s.venue_slug
                    WHERE  ts.song_slug = $1
                    ORDER  BY tr.show_date DESC
                    LIMIT  $2
                    """,
                    slug,
                    limit,
                )
            )

    # ------------------------------------------------------------------
    # Jam chart
    # ------------------------------------------------------------------

    async def jam_chart(self, year: int | None = None, limit: int = 50) -> list[asyncpg.Record]:
        """Return jam-chart entries, optionally filtered by year."""
        args: list[Any] = []
        year_clause = ""
        if year is not None:
            year_clause = "AND EXTRACT(YEAR FROM jc.show_date) = $1"
            args.append(year)
        args.append(limit)
        limit_idx = len(args)

        # `year_clause` and `limit_idx` are derived from internal counters/
        # constants, never from user input. All values use asyncpg params.
        sql = f"""
            SELECT jc.show_date AS date,
                   jc.song_slug,
                   jc.song_name,
                   jc.notes,
                   s.show_id_phishin,
                   s.show_id_phishnet,
                   v.name AS venue_name
            FROM   jam_chart_entries jc
            JOIN   shows  s ON s.date = jc.show_date
            LEFT JOIN venues v ON v.slug = s.venue_slug
            WHERE  1=1
            {year_clause}
            ORDER  BY jc.show_date DESC
            LIMIT  ${limit_idx}
        """  # noqa: S608 — values pass through asyncpg params, fragments are internal
        async with self._pool.acquire() as conn:
            return list(await conn.fetch(sql, *args))

    # ------------------------------------------------------------------
    # Reviews
    # ------------------------------------------------------------------

    async def get_reviews(self, show_date: str, limit: int = 25) -> list[asyncpg.Record]:
        """Return reviews for a show date."""
        async with self._pool.acquire() as conn:
            return list(
                await conn.fetch(
                    """
                    SELECT id, show_date, upstream_review_id,
                           username, score, review_text, posted_at
                    FROM   reviews
                    WHERE  show_date = $1::date
                    ORDER  BY posted_at DESC NULLS LAST
                    LIMIT  $2
                    """,
                    show_date,
                    limit,
                )
            )

    # ------------------------------------------------------------------
    # Audio
    # ------------------------------------------------------------------

    async def get_audio(
        self, show_date_or_id: str
    ) -> tuple[asyncpg.Record | None, list[asyncpg.Record]]:
        """Return (show_row, tracks) for the audio bundle of a show."""
        async with self._pool.acquire() as conn:
            if _is_date(show_date_or_id):
                show_row: asyncpg.Record | None = await conn.fetchrow(
                    """
                    SELECT s.date, s.show_id_phishin, s.duration_ms,
                           s.audio_status, s.album_zip_url, s.cover_art_url_large,
                           v.name AS venue_name, v.location AS venue_location
                    FROM   shows s
                    LEFT JOIN venues v ON v.slug = s.venue_slug
                    WHERE  s.date = $1::date
                    """,
                    show_date_or_id,
                )
            else:
                try:
                    show_id_int = int(show_date_or_id)
                except ValueError:
                    return None, []
                show_row = await conn.fetchrow(
                    """
                    SELECT s.date, s.show_id_phishin, s.duration_ms,
                           s.audio_status, s.album_zip_url, s.cover_art_url_large,
                           v.name AS venue_name, v.location AS venue_location
                    FROM   shows s
                    LEFT JOIN venues v ON v.slug = s.venue_slug
                    WHERE  s.show_id_phishin = $1
                    """,
                    show_id_int,
                )

            if show_row is None:
                return None, []

            show_date = str(show_row["date"])
            tracks: list[asyncpg.Record] = await conn.fetch(
                """
                SELECT id, show_date, slug, title, position, set_name,
                       duration_ms, mp3_url, waveform_image_url
                FROM   tracks
                WHERE  show_date = $1::date
                ORDER  BY position
                """,
                show_date,
            )

        return show_row, tracks

    async def get_track(self, track_id: int) -> asyncpg.Record | None:
        """Return a single track by its phish.in id."""
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(
                """
                SELECT tr.id, tr.show_date, tr.slug, tr.title,
                       tr.position, tr.set_name, tr.duration_ms,
                       tr.mp3_url, tr.waveform_image_url,
                       s.show_id_phishin,
                       v.name     AS venue_name,
                       v.location AS venue_location
                FROM   tracks tr
                JOIN   shows  s  ON s.date = tr.show_date
                LEFT JOIN venues v ON v.slug = s.venue_slug
                WHERE  tr.id = $1
                """,
                track_id,
            )

    async def search_audio_tracks(self, song_slug: str, limit: int = 20) -> list[asyncpg.Record]:
        """Return every track for a given song slug, joined to shows + venues."""
        async with self._pool.acquire() as conn:
            return list(
                await conn.fetch(
                    """
                    SELECT tr.id, tr.show_date, tr.slug, tr.title,
                           tr.position, tr.set_name, tr.duration_ms,
                           tr.mp3_url, tr.waveform_image_url,
                           s.show_id_phishin,
                           v.name     AS venue_name,
                           v.location AS venue_location
                    FROM   tracks tr
                    JOIN   shows  s  ON s.date = tr.show_date
                    LEFT JOIN venues v ON v.slug = s.venue_slug
                    WHERE  tr.slug = $1
                    ORDER  BY tr.show_date DESC
                    LIMIT  $2
                    """,
                    song_slug,
                    limit,
                )
            )

    # ------------------------------------------------------------------
    # Analytical tools (vault-only)
    # ------------------------------------------------------------------

    async def venue_history(self, venue_slug: str, limit: int = 25) -> list[asyncpg.Record]:
        """Return shows at a given venue, newest first."""
        async with self._pool.acquire() as conn:
            return list(
                await conn.fetch(
                    """
                    SELECT s.date, s.show_id_phishin, s.show_id_phishnet,
                           v.name AS venue_name, v.location,
                           t.name AS tour_name
                    FROM   shows s
                    LEFT JOIN venues v ON v.slug = s.venue_slug
                    LEFT JOIN tours  t ON t.slug = s.tour_slug
                    WHERE  s.venue_slug = $1
                    ORDER  BY s.date DESC
                    LIMIT  $2
                    """,
                    venue_slug,
                    limit,
                )
            )

    async def songs_by_gap(self, limit: int = 25) -> list[asyncpg.Record]:
        """Return songs ordered by current gap (shows since last play), descending."""
        async with self._pool.acquire() as conn:
            return list(
                await conn.fetch(
                    """
                    SELECT slug, title,
                           tracks_count AS times_played,
                           gap_current,
                           last_play_date
                    FROM   songs
                    WHERE  gap_current IS NOT NULL
                    ORDER  BY gap_current DESC
                    LIMIT  $1
                    """,
                    limit,
                )
            )

    # ------------------------------------------------------------------
    # ETL health
    # ------------------------------------------------------------------

    async def last_etl_run(self) -> dict[str, object] | None:
        """Return the most recent etl_runs row as a plain dict, or None."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, started_at, finished_at, mode, status,
                       rows_added, rows_updated
                FROM   etl_runs
                ORDER  BY id DESC
                LIMIT  1
                """
            )
        if row is None:
            return None
        return dict(row)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _is_date(value: str) -> bool:
    """Return True if value looks like YYYY-MM-DD."""
    return len(value) == 10 and value.count("-") == 2
