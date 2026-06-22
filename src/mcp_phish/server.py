"""MCP Phish — wraps api.phish.net v5 + phish.in v2 behind a typed tool surface.

Fifteen tools across five data-domain modules:

* shows   — search_shows, get_show, recent_shows
* songs   — search_songs, get_song, validate_song_slugs, song_history, jam_chart
* audio   — get_audio, get_track, search_audio_tracks
* extras  — get_reviews, venue_history, songs_by_gap
* health  — health

``build_server`` wires the upstream clients, response cache, throttles, and
vault reader, bundles them into a :class:`mcp_phish._context.ServerContext`, and
hands the context to each module's ``register(mcp, ctx)`` entrypoint. Tool
definitions live under :mod:`mcp_phish.modules`; the row→model projection
helpers live in :mod:`mcp_phish.mappers`; the response-envelope helpers live in
:mod:`mcp_phish._common`.

Returns are projected through the public Pydantic models in ``models.py`` so the
wire format stays identical across stub mode, live mode, and the Phase 3 vault
swap.

Transport: Streamable HTTP via FastMCP (current MCP spec).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from fastmcp import FastMCP

from mcp_phish._context import ServerContext
from mcp_phish.cache import ResponseCache
from mcp_phish.clients.stubs import StubPhishInClient, StubPhishNetClient
from mcp_phish.config import Settings, load_settings
from mcp_phish.logging_setup import configure_logging
from mcp_phish.mappers import _PhishInLike, _PhishNetLike
from mcp_phish.modules import register_modules
from mcp_phish.throttle import TokenBucket
from mcp_phish.vault import VaultReader

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger("mcp_phish.server")


def build_server(
    settings: Settings,
    *,
    phishnet_client: _PhishNetLike | None = None,
    phishin_client: _PhishInLike | None = None,
    cache: ResponseCache | None = None,
    phishnet_throttle: TokenBucket | None = None,
    phishin_throttle: TokenBucket | None = None,
    vault_reader: VaultReader | None = None,
    vault_pool: asyncpg.Pool | None = None,
) -> FastMCP:
    """Build a fully-wired FastMCP instance.

    Tests can pass in their own stubs/throttles/cache/vault_reader to keep
    behavior isolated. Production calls ``main()`` which relies on lazy-init
    for the vault pool.

    ``vault_reader`` takes precedence over ``vault_pool`` when both are given.
    When ``vault_pool`` is given, a ``VaultReader`` is constructed from it.
    When neither is given and ``settings.vault_enabled`` is True, the pool is
    created lazily on the first vault read.
    """
    pn_throttle = phishnet_throttle or TokenBucket(rps=settings.throttle_phishnet_rps)
    pi_throttle = phishin_throttle or TokenBucket(rps=settings.throttle_phishin_rps)

    pn: _PhishNetLike
    pi: _PhishInLike
    if phishnet_client is not None:
        pn = phishnet_client
    elif settings.stub_mode:
        pn = StubPhishNetClient()
    else:
        # Lazy import keeps module-level surface clean for tests.
        from mcp_phish.clients.phishnet import PhishNetClient

        pn = PhishNetClient(
            api_key=settings.phishnet_api_key,
            throttle=pn_throttle,
            base_url=settings.phishnet_base_url,
        )
    if phishin_client is not None:
        pi = phishin_client
    elif settings.stub_mode:
        pi = StubPhishInClient()
    else:
        from mcp_phish.clients.phishin import PhishInClient

        pi = PhishInClient(
            api_key=settings.phishin_api_key,
            throttle=pi_throttle,
            base_url=settings.phishin_base_url,
        )

    response_cache = cache or ResponseCache(
        db_path=settings.cache_db_path,
        ttl_seconds=settings.cache_ttl_seconds,
    )

    # --- vault reader setup ------------------------------------------------
    # Priority: explicit vault_reader > vault_pool > lazy-init on first use
    _vault_reader: VaultReader | None
    if vault_reader is not None:
        _vault_reader = vault_reader
    elif vault_pool is not None:
        _vault_reader = VaultReader(vault_pool)
    else:
        _vault_reader = None  # will be created lazily if vault_enabled

    ctx = ServerContext(
        settings,
        pn=pn,
        pi=pi,
        response_cache=response_cache,
        pn_throttle=pn_throttle,
        pi_throttle=pi_throttle,
        vault_reader=_vault_reader,
        started_at=time.time(),
    )

    mcp = FastMCP("Phish")
    register_modules(mcp, ctx)
    return mcp


# ---------------------------------------------------------------------------
# Module-level entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entrypoint used by the Docker image."""
    settings = load_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    logger.info("MCP Phish starting", extra={"config": settings.safe_repr()})
    server = build_server(settings)
    server.run(
        transport="streamable-http",
        host=settings.mcp_host,
        port=settings.mcp_port,
    )


if __name__ == "__main__":
    main()
