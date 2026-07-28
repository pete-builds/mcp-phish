"""The one place every tool this server exposes is written down.

``TOOL_INDEX`` is the answer to "what does this server expose?" — a single
file read, no grepping for ``@mcp.tool()`` across four modules. It is not
documentation: ``tests/test_tool_index.py`` asserts it matches exactly what
FastMCP registered, so a tool added, renamed, or removed without touching
this file fails CI.

Adding a tool means three edits in the same commit: the tool body in its
domain module, its name in ``TOOL_INDEX``, and (if it is a new module) the
registrar in ``_REGISTRARS``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from mcp_phish import runtime
from mcp_phish.runtime import ServerContext
from mcp_phish.tools import audio, shows, songs, stats

if TYPE_CHECKING:
    from fastmcp import FastMCP

__all__ = ["ALL_TOOL_NAMES", "TOOL_INDEX", "register_all"]


#: Module import path → the tool names that module registers.
#: Ordered the way a reader would want to skim them, not alphabetically.
TOOL_INDEX: dict[str, tuple[str, ...]] = {
    "mcp_phish.tools.shows": (
        "search_shows",
        "get_show",
        "recent_shows",
        "get_reviews",
        "venue_history",
    ),
    "mcp_phish.tools.songs": (
        "search_songs",
        "get_song",
        "validate_song_slugs",
        "song_history",
        "jam_chart",
        "songs_by_gap",
    ),
    "mcp_phish.tools.audio": (
        "get_audio",
        "get_track",
        "search_audio_tracks",
    ),
    "mcp_phish.tools.stats": ("stats_overview",),
    # health reads runtime state, not a data domain — see mcp_phish.runtime.
    "mcp_phish.runtime": ("health",),
}

#: Flattened view of TOOL_INDEX, for callers that just want the names.
ALL_TOOL_NAMES: frozenset[str] = frozenset(name for names in TOOL_INDEX.values() for name in names)

#: Registrars, in TOOL_INDEX key order.
_REGISTRARS: tuple[Callable[[FastMCP, ServerContext], None], ...] = (
    shows.register,
    songs.register,
    audio.register,
    stats.register,
    runtime.register,
)


def register_all(mcp: FastMCP, ctx: ServerContext) -> None:
    """Register every tool in :data:`TOOL_INDEX` against ``mcp``."""
    for register in _REGISTRARS:
        register(mcp, ctx)
