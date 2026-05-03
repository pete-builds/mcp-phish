"""Realistic stub responses for api.phish.net v5 and phish.in v2.

Used when ``stub_mode=True`` (the default) so the server boots and every tool
returns sensible data without a live API key. Payloads mirror the upstream
shape closely enough that the projection layer in ``server.py`` cannot tell
the difference, which keeps the public Pydantic contract identical between
modes.

State is in-memory and read-only. The MCP is a read-only server in Phase 1;
nothing in the stubs needs to mutate.

The reference show (12/30/95 at MSG) is a deliberate choice — it's a famous
date phans recognize and useful for end-to-end smoke tests.
"""

from __future__ import annotations

from typing import Any

from mcp_phish.clients.phishin import PhishInError
from mcp_phish.clients.phishnet import PhishNetError

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

_PHISHNET_SHOWS: list[dict[str, Any]] = [
    {
        "showid": "1252691618",
        "showdate": "1995-12-30",
        "venue": "Madison Square Garden",
        "city": "New York",
        "state": "NY",
        "country": "USA",
        "tour_name": "1995 Fall Tour",
        "rating": 4.7,
        "rating_count": 412,
        "review_count": 38,
        "setlistnotes": "The Mike's Groove ranks among the most beloved of the era.",
    },
    {
        "showid": "1252699999",
        "showdate": "1997-11-17",
        "venue": "McNichols Arena",
        "city": "Denver",
        "state": "CO",
        "country": "USA",
        "tour_name": "Fall Tour 1997",
        "rating": 4.9,
        "rating_count": 588,
        "review_count": 71,
        "setlistnotes": "Officially released as Live Phish 11. Type II Ghost into Tweezer.",
    },
    {
        "showid": "1252690001",
        "showdate": "2024-12-31",
        "venue": "Madison Square Garden",
        "city": "New York",
        "state": "NY",
        "country": "USA",
        "tour_name": "2024 NYE Run",
        "rating": 4.5,
        "rating_count": 220,
        "review_count": 22,
        "setlistnotes": "NYE gag involved a giant clock dropping from the rafters.",
    },
]

_PHISHNET_SETLISTS: dict[str, list[dict[str, Any]]] = {
    "1995-12-30": [
        {
            "position": 1,
            "set": "1",
            "song": "Makisupa Policeman",
            "slug": "makisupa-policeman",
            "trans_mark": " > ",
            "footnote": "",
        },
        {
            "position": 2,
            "set": "1",
            "song": "Punch You In the Eye",
            "slug": "punch-you-in-the-eye",
            "trans_mark": ", ",
            "footnote": "",
        },
        {
            "position": 3,
            "set": "1",
            "song": "Reba",
            "slug": "reba",
            "trans_mark": ", ",
            "footnote": "",
        },
        {
            "position": 4,
            "set": "2",
            "song": "Mike's Song",
            "slug": "mikes-song",
            "trans_mark": " > ",
            "footnote": "",
        },
        {
            "position": 5,
            "set": "2",
            "song": "Simple",
            "slug": "simple",
            "trans_mark": " > ",
            "footnote": "",
        },
        {
            "position": 6,
            "set": "2",
            "song": "Weekapaug Groove",
            "slug": "weekapaug-groove",
            "trans_mark": "",
            "footnote": "",
        },
        {
            "position": 7,
            "set": "e",
            "song": "Fluffhead",
            "slug": "fluffhead",
            "trans_mark": "",
            "footnote": "",
        },
    ],
    "1997-11-17": [
        {
            "position": 1,
            "set": "1",
            "song": "Tweezer",
            "slug": "tweezer",
            "trans_mark": " > ",
            "footnote": "",
        },
        {
            "position": 2,
            "set": "1",
            "song": "Ghost",
            "slug": "ghost",
            "trans_mark": "",
            "footnote": "Type II jam, ~30 minutes.",
        },
    ],
    "2024-12-31": [
        {
            "position": 1,
            "set": "1",
            "song": "Auld Lang Syne",
            "slug": "auld-lang-syne",
            "trans_mark": "",
            "footnote": "NYE midnight.",
        },
    ],
}

_PHISHNET_SONGS: list[dict[str, Any]] = [
    {
        "slug": "fluffhead",
        "title": "Fluffhead",
        "artist": "Phish",
        "isoriginal": 1,
        "times_played": 264,
        "debut": "1986-04-15",
        "last_played": "2024-12-31",
        "gap": 0,
    },
    {
        "slug": "tweezer",
        "title": "Tweezer",
        "artist": "Phish",
        "isoriginal": 1,
        "times_played": 622,
        "debut": "1990-04-09",
        "last_played": "2024-12-31",
        "gap": 0,
    },
    {
        "slug": "mikes-song",
        "title": "Mike's Song",
        "artist": "Phish",
        "isoriginal": 1,
        "times_played": 588,
        "debut": "1989-09-12",
        "last_played": "2024-12-31",
        "gap": 0,
    },
    {
        "slug": "ghost",
        "title": "Ghost",
        "artist": "Phish",
        "isoriginal": 1,
        "times_played": 312,
        "debut": "1997-06-13",
        "last_played": "2024-08-31",
        "gap": 6,
    },
    {
        "slug": "reba",
        "title": "Reba",
        "artist": "Phish",
        "isoriginal": 1,
        "times_played": 256,
        "debut": "1990-09-21",
        "last_played": "2024-08-30",
        "gap": 7,
    },
    {
        "slug": "satisfaction",
        "title": "(I Can't Get No) Satisfaction",
        "artist": "The Rolling Stones",
        "isoriginal": 0,
        "times_played": 1,
        "debut": "1989-10-22",
        "last_played": "1989-10-22",
        "gap": 1860,
    },
]

_PHISHNET_PERFORMANCES: dict[str, list[dict[str, Any]]] = {
    "fluffhead": [
        {
            "showid": "1252691618",
            "showdate": "1995-12-30",
            "venue": "Madison Square Garden",
            "city": "New York",
            "state": "NY",
            "set": "e",
            "trans_mark": "",
            "gap": 4,
        },
        {
            "showid": "1252690001",
            "showdate": "2024-12-31",
            "venue": "Madison Square Garden",
            "city": "New York",
            "state": "NY",
            "set": "1",
            "trans_mark": "",
            "gap": 12,
        },
    ],
    "tweezer": [
        {
            "showid": "1252699999",
            "showdate": "1997-11-17",
            "venue": "McNichols Arena",
            "city": "Denver",
            "state": "CO",
            "set": "1",
            "trans_mark": " > ",
            "gap": 3,
        },
    ],
}

_PHISHNET_JAMCHART: list[dict[str, Any]] = [
    {
        "showid": "1252699999",
        "showdate": "1997-11-17",
        "venue": "McNichols Arena",
        "song": "Tweezer",
        "slug": "tweezer",
        "notes": "Iconic Type II jam. Often cited as the peak of Fall '97.",
    },
    {
        "showid": "1252691618",
        "showdate": "1995-12-30",
        "venue": "Madison Square Garden",
        "song": "Mike's Song",
        "slug": "mikes-song",
        "notes": "Beloved Mike's Groove from the '95 NYE run.",
    },
]

_PHISHNET_REVIEWS: dict[str, list[dict[str, Any]]] = {
    "1995-12-30": [
        {
            "reviewid": "r-19951230-1",
            "showid": "1252691618",
            "showdate": "1995-12-30",
            "username": "phan42",
            "posted_at": "1996-01-05T10:00:00Z",
            "score": 5.0,
            "review": "Best Mike's Groove of all time. The Simple peaked unbelievably hard.",
        },
        {
            "reviewid": "r-19951230-2",
            "showid": "1252691618",
            "showdate": "1995-12-30",
            "username": "tweezerfan",
            "posted_at": "1996-01-12T14:22:00Z",
            "score": 4.5,
            "review": "Set 2 is essential listening. First set drags but the encore redeems it.",
        },
    ],
    "1997-11-17": [
        {
            "reviewid": "r-19971117-1",
            "showid": "1252699999",
            "showdate": "1997-11-17",
            "username": "denverhead",
            "posted_at": "1997-11-19T08:14:00Z",
            "score": 5.0,
            "review": "If you only listen to one show from Fall '97, make it this one.",
        },
    ],
}

# ---------------------------------------------------------------------------
# phish.in fixtures (mirrors the live v2 envelope shapes)
# ---------------------------------------------------------------------------

_PHISHIN_SHOWS: dict[str, dict[str, Any]] = {
    "1995-12-30": {
        "id": 412,
        "date": "1995-12-30",
        "audio_status": "complete",
        "duration": 8580000,
        "tour_name": "1995 Fall Tour",
        "venue_name": "Madison Square Garden",
        "venue": {
            "slug": "madison-square-garden",
            "name": "Madison Square Garden",
            "city": "New York",
            "state": "NY",
            "country": "USA",
            "location": "New York, NY",
            "latitude": 40.7505,
            "longitude": -73.9934,
        },
        "album_zip_url": "https://phish.in/blob/stub-1995-12-30.zip",
        "cover_art_urls": {"large": "https://phish.in/blob/stub-1995-12-30.jpg"},
        "tracks": [
            {
                "id": 50001,
                "slug": "fluffhead",
                "title": "Fluffhead",
                "position": 7,
                "duration": 920000,
                "set_name": "Encore",
                "mp3_url": "https://phish.in/blob/stub-fluffhead-19951230.mp3",
                "waveform_image_url": "https://phish.in/blob/stub-fluffhead-19951230.png",
                "venue_slug": "madison-square-garden",
                "venue_name": "Madison Square Garden",
                "venue_location": "New York, NY",
                "show_date": "1995-12-30",
            },
        ],
    },
    "1997-11-17": {
        "id": 412412,
        "date": "1997-11-17",
        "audio_status": "complete",
        "duration": 8971076,
        "tour_name": "Fall Tour 1997",
        "venue_name": "McNichols Arena",
        "venue": {
            "slug": "mcnichols-arena",
            "name": "McNichols Arena",
            "city": "Denver",
            "state": "CO",
            "country": "USA",
            "location": "Denver, CO",
            "latitude": 39.7486,
            "longitude": -105.0070,
        },
        "album_zip_url": "https://phish.in/blob/stub-1997-11-17.zip",
        "cover_art_urls": {"large": "https://phish.in/blob/stub-1997-11-17.jpg"},
        "tracks": [
            {
                "id": 60001,
                "slug": "tweezer",
                "title": "Tweezer",
                "position": 1,
                "duration": 1750000,
                "set_name": "Set 1",
                "mp3_url": "https://phish.in/blob/stub-tweezer-19971117.mp3",
                "waveform_image_url": "https://phish.in/blob/stub-tweezer-19971117.png",
                "venue_slug": "mcnichols-arena",
                "venue_name": "McNichols Arena",
                "venue_location": "Denver, CO",
                "show_date": "1997-11-17",
            },
            {
                "id": 60002,
                "slug": "ghost",
                "title": "Ghost",
                "position": 2,
                "duration": 1840000,
                "set_name": "Set 1",
                "mp3_url": "https://phish.in/blob/stub-ghost-19971117.mp3",
                "waveform_image_url": "https://phish.in/blob/stub-ghost-19971117.png",
                "venue_slug": "mcnichols-arena",
                "venue_name": "McNichols Arena",
                "venue_location": "Denver, CO",
                "show_date": "1997-11-17",
            },
        ],
    },
}

_PHISHIN_TRACKS: dict[int, dict[str, Any]] = {
    50001: _PHISHIN_SHOWS["1995-12-30"]["tracks"][0],
    60001: _PHISHIN_SHOWS["1997-11-17"]["tracks"][0],
    60002: _PHISHIN_SHOWS["1997-11-17"]["tracks"][1],
}


# ---------------------------------------------------------------------------
# Stub clients (drop-in replacements with the same async signatures)
# ---------------------------------------------------------------------------


class StubPhishNetClient:
    """In-memory phish.net stub. Same async surface as :class:`PhishNetClient`."""

    def __init__(self) -> None:
        self.api_key = "stub"
        self.base_url = "stub://phishnet"
        self._calls: int = 0  # how many fake calls have been made

    async def aclose(self) -> None:
        return None

    async def get_show_by_date(self, date: str) -> Any:
        self._calls += 1
        for show in _PHISHNET_SHOWS:
            if show["showdate"] == date:
                return [show]
        raise PhishNetError(f"phish.net stub: no show for date {date}")

    async def get_show_by_id(self, show_id: str) -> Any:
        self._calls += 1
        for show in _PHISHNET_SHOWS:
            if show["showid"] == show_id:
                return [show]
        raise PhishNetError(f"phish.net stub: no show for id {show_id}")

    async def search_shows(self, params: dict[str, Any]) -> Any:
        self._calls += 1
        results = list(_PHISHNET_SHOWS)

        def _matches(show: dict[str, Any]) -> bool:
            if (yr := params.get("year")) and not str(show["showdate"]).startswith(str(yr)):
                return False
            if (city := params.get("city")) and city.lower() not in show["city"].lower():
                return False
            if (st := params.get("state")) and st.lower() != show["state"].lower():
                return False
            if (country := params.get("country")) and country.lower() != show["country"].lower():
                return False
            return not (
                (venue := params.get("venue")) and venue.lower() not in show["venue"].lower()
            )

        return [s for s in results if _matches(s)]

    async def get_setlist_by_date(self, date: str) -> Any:
        self._calls += 1
        rows = _PHISHNET_SETLISTS.get(date)
        if rows is None:
            return []
        # Inject the showid for each row, like the real API does.
        showid = next((s["showid"] for s in _PHISHNET_SHOWS if s["showdate"] == date), "")
        return [{**row, "showid": showid, "showdate": date} for row in rows]

    async def list_songs(self, params: dict[str, Any] | None = None) -> Any:
        self._calls += 1
        return list(_PHISHNET_SONGS)

    async def get_song_by_slug(self, slug: str) -> Any:
        self._calls += 1
        for song in _PHISHNET_SONGS:
            if song["slug"] == slug:
                return [song]
        raise PhishNetError(f"phish.net stub: no song with slug {slug!r}")

    async def search_songs(self, params: dict[str, Any]) -> Any:
        self._calls += 1
        query = str(params.get("query") or "").lower()
        if not query:
            return list(_PHISHNET_SONGS)
        return [
            s for s in _PHISHNET_SONGS if query in s["slug"].lower() or query in s["title"].lower()
        ]

    async def song_performances(self, slug: str, params: dict[str, Any] | None = None) -> Any:
        self._calls += 1
        return list(_PHISHNET_PERFORMANCES.get(slug, []))

    async def jam_chart(self, params: dict[str, Any] | None = None) -> Any:
        self._calls += 1
        rows = list(_PHISHNET_JAMCHART)
        if params and (yr := params.get("year")):
            rows = [r for r in rows if str(r["showdate"]).startswith(str(yr))]
        return rows

    async def reviews_by_date(self, date: str) -> Any:
        self._calls += 1
        return list(_PHISHNET_REVIEWS.get(date, []))

    async def reviews_by_id(self, show_id: str) -> Any:
        self._calls += 1
        for rows in _PHISHNET_REVIEWS.values():
            if rows and rows[0]["showid"] == show_id:
                return list(rows)
        return []


class StubPhishInClient:
    """In-memory phish.in stub. Same async surface as :class:`PhishInClient`."""

    def __init__(self) -> None:
        self.api_key = ""
        self.base_url = "stub://phishin"
        self._calls: int = 0

    async def aclose(self) -> None:
        return None

    async def get_show(self, date_or_id: str) -> Any:
        self._calls += 1
        if date_or_id in _PHISHIN_SHOWS:
            return _PHISHIN_SHOWS[date_or_id]
        # Try id-based lookup
        for show in _PHISHIN_SHOWS.values():
            if str(show["id"]) == str(date_or_id):
                return show
        raise PhishInError(f"phish.in stub: no show for {date_or_id}")

    async def get_track(self, track_id: int) -> Any:
        self._calls += 1
        track = _PHISHIN_TRACKS.get(int(track_id))
        if track is None:
            raise PhishInError(f"phish.in stub: no track id {track_id}")
        return track

    async def search_tracks(self, params: dict[str, Any]) -> Any:
        self._calls += 1
        slug = params.get("slug") or params.get("song_slug") or ""
        per_page = int(params.get("per_page") or 20)
        matches: list[dict[str, Any]] = [t for t in _PHISHIN_TRACKS.values() if t["slug"] == slug]
        return {
            "tracks": matches[:per_page],
            "total_pages": 1,
            "current_page": 1,
            "total_entries": len(matches),
        }
