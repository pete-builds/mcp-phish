"""Public Pydantic models for mcp-phish — THE FROZEN PHASE 1 CONTRACT.

Every tool returns one of these types. These shapes are the public API of the
MCP server and they MUST stay byte-identical across the future Phase 3
vault swap. The source can change (live API → Postgres vault); the shape
exposed to MCP clients never can.

Design notes:

* All models use ``model_config = ConfigDict(frozen=True, extra="forbid")``
  so any drift in the upstream API surfaces as a validation failure rather
  than silently leaking new fields into the contract.
* Fields are projections of the upstream response, NOT raw passthroughs. A
  client sees a stable shape regardless of which source produced it.
* Optional fields use ``None`` defaults; lists default to ``[]``. Empty is
  always an explicit value, never an absent key.
* All datetimes are returned as ISO 8601 UTC strings via ``str``. Pydantic
  will coerce; we don't need ``datetime`` typing for the wire format.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Helper config
# ---------------------------------------------------------------------------

_FROZEN: ConfigDict = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------
# Show models (phish.net)
# ---------------------------------------------------------------------------


class Venue(BaseModel):
    """A venue, normalized across phish.net + phish.in."""

    model_config = _FROZEN

    slug: str = ""
    name: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    location: str = ""  # human-readable "City, ST"
    latitude: float | None = None
    longitude: float | None = None


class ShowSummary(BaseModel):
    """Lightweight show record for list endpoints."""

    model_config = _FROZEN

    show_id: str  # phish.net showid OR phish.in id, whichever is canonical
    date: str  # YYYY-MM-DD
    venue_name: str = ""
    location: str = ""
    tour_name: str = ""


class SetlistEntry(BaseModel):
    """One song in a setlist, with its position and any segue/note metadata."""

    model_config = _FROZEN

    position: int
    set_name: str  # "Set 1", "Set 2", "Encore", etc.
    song_slug: str
    song_title: str
    transition: str = ""  # ">", "->", "" (no segue)
    footnote: str = ""


class Show(BaseModel):
    """Full show: setlist + venue + ratings + review snippets."""

    model_config = _FROZEN

    show_id: str
    date: str
    venue: Venue
    tour_name: str = ""
    setlist: list[SetlistEntry] = Field(default_factory=list)
    rating: float | None = None  # phish.net 5-star average, when available
    rating_count: int = 0
    review_count: int = 0
    setlist_notes: str = ""


# ---------------------------------------------------------------------------
# Song models (phish.net)
# ---------------------------------------------------------------------------


class SongSummary(BaseModel):
    """Lightweight song record for search results."""

    model_config = _FROZEN

    slug: str
    title: str
    artist: str | None = None
    original: bool = True
    times_played: int = 0


class Song(BaseModel):
    """Detailed song record: debut, last play, gap, total."""

    model_config = _FROZEN

    slug: str
    title: str
    artist: str | None = None
    original: bool = True
    times_played: int = 0
    debut_date: str | None = None
    last_played_date: str | None = None
    gap: int | None = None  # shows since last play


class Performance(BaseModel):
    """One performance of a song — used by song_history()."""

    model_config = _FROZEN

    show_id: str
    date: str
    venue_name: str = ""
    location: str = ""
    set_name: str = ""
    transition: str = ""
    gap: int | None = None  # gap from prior performance


class NotableJam(BaseModel):
    """A jam-chart entry: a notable performance flagged by phish.net editors."""

    model_config = _FROZEN

    show_id: str
    date: str
    song_slug: str
    song_title: str
    venue_name: str = ""
    notes: str = ""


# ---------------------------------------------------------------------------
# Review model (phish.net)
# ---------------------------------------------------------------------------


class Review(BaseModel):
    """A user review of a show."""

    model_config = _FROZEN

    review_id: str
    show_id: str
    date: str  # show date
    author: str = ""  # username or display name
    posted_at: str | None = None  # ISO 8601 of when the review was written
    rating: float | None = None
    text: str = ""


# ---------------------------------------------------------------------------
# Audio models (phish.in)
# ---------------------------------------------------------------------------


class Track(BaseModel):
    """A single audio track from phish.in."""

    model_config = _FROZEN

    track_id: int
    slug: str  # song slug
    title: str  # song title at this performance
    show_id: str  # phish.in show id, as a string (kept consistent with Show.show_id)
    show_date: str  # YYYY-MM-DD
    set_name: str = ""
    position: int = 0
    duration_ms: int = 0
    mp3_url: str | None = None
    waveform_image_url: str | None = None
    venue_name: str = ""
    venue_location: str = ""


class ShowAudio(BaseModel):
    """Audio bundle for a single show: track list + show-level art/zip."""

    model_config = _FROZEN

    show_id: str
    date: str
    venue_name: str = ""
    venue_location: str = ""
    duration_ms: int = 0
    audio_status: str = ""  # "complete" / "partial" / etc.
    album_zip_url: str | None = None
    cover_art_url: str | None = None
    tracks: list[Track] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Health model (meta)
# ---------------------------------------------------------------------------


class UpstreamHealth(BaseModel):
    """Per-upstream health snapshot, surfaced in health()."""

    model_config = _FROZEN

    reachable: bool
    rps_limit: float
    tokens_available: float
    last_call_ts: str | None = None  # ISO 8601


class CacheHealth(BaseModel):
    """Cache snapshot, surfaced in health()."""

    model_config = _FROZEN

    path: str
    size_bytes: int
    ttl_seconds: int
    last_hit_ts: str | None = None
    last_miss_ts: str | None = None


class Health(BaseModel):
    """Top-level health summary."""

    model_config = _FROZEN

    status: str  # "ok" | "degraded"
    stub_mode: bool
    version: str
    phishnet: UpstreamHealth
    phishin: UpstreamHealth
    cache: CacheHealth
