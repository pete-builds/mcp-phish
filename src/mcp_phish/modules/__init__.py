"""Tool modules for mcp-phish.

Each module exposes ``register(mcp, ctx)``, which wires its own ``@mcp.tool()``
definitions onto the FastMCP instance using the shared
:class:`mcp_phish._context.ServerContext`. :func:`register_modules` imports each
module and calls its entrypoint in a stable order (the order tools appear in
tool-list output): shows, songs, audio, extras, health.

This mirrors the ``register()``-per-module dispatcher pattern from mcp-unifi.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_phish.modules import audio, extras, health, shows, songs

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from mcp_phish._context import ServerContext


def register_modules(mcp: FastMCP, ctx: ServerContext) -> tuple[str, ...]:
    """Register every tool module on ``mcp``.

    Returns the tuple of module names registered, in order. Order matters only
    for clarity in tool-list output: phish.net show queries first, then the
    song catalog, then phish.in audio, then reviews + vault analytics, then the
    health meta tool.
    """
    shows.register(mcp, ctx)
    songs.register(mcp, ctx)
    audio.register(mcp, ctx)
    extras.register(mcp, ctx)
    health.register(mcp, ctx)
    return ("shows", "songs", "audio", "extras", "health")


__all__ = ["register_modules"]
