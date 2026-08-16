"""MCP Phish — wraps api.phish.net v5 + phish.in v2 behind a typed tool surface.

This module is deliberately thin: it wires a :class:`~mcp_phish.runtime.ServerContext`,
hands it to :func:`mcp_phish.tools.register_all`, and runs the transport.

Where things live:

* ``mcp_phish.responses``  — the wire contract (``ok`` / ``err``)
* ``mcp_phish.runtime``    — ServerContext (clients, cache, throttles, vault,
                             hot-window) plus the ``health`` readout of it
* ``mcp_phish.vault``      — vault data access
* ``mcp_phish.mappers.*``  — one projection module per source shape
* ``mcp_phish.tools``      — ``TOOL_INDEX``: every exposed tool, in one place
* ``mcp_phish.tools.*``    — tool bodies, one module per domain

Returns are projected through the public Pydantic models in ``models.py`` so
the wire format stays identical across stub mode, live mode, and vault mode.

Transport: Streamable HTTP via FastMCP (current MCP spec).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_phish.cache import ResponseCache
from mcp_phish.config import Settings, load_settings
from mcp_phish.logging_setup import configure_logging
from mcp_phish.runtime import PhishInLike, PhishNetLike, build_context
from mcp_phish.throttle import TokenBucket
from mcp_phish.tools import register_all
from mcp_phish.vault import VaultReader

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger("mcp_phish.server")

__all__ = ["build_server", "main"]


def build_server(
    settings: Settings,
    *,
    phishnet_client: PhishNetLike | None = None,
    phishin_client: PhishInLike | None = None,
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
    ctx = build_context(
        settings,
        phishnet_client=phishnet_client,
        phishin_client=phishin_client,
        cache=cache,
        phishnet_throttle=phishnet_throttle,
        phishin_throttle=phishin_throttle,
        vault_reader=vault_reader,
        vault_pool=vault_pool,
    )
    mcp = FastMCP("Phish")

    @mcp.custom_route("/health", methods=["GET"])
    async def health_route(_request: Request) -> JSONResponse:
        """Lightweight liveness endpoint for the Docker HEALTHCHECK.

        Separate from the ``health`` MCP tool below: a bare GET here never
        touches the ``/mcp`` transport, so it never mints a streamable-http
        session. Probing ``/mcp`` directly does — the SDK creates a transport
        session before it returns 4xx, and nothing reaps it, leaking ~40 KB
        per probe at the standard 30s interval. This route sidesteps that
        entirely; gate on the 200 status code only, not the body.
        """
        return JSONResponse({"status": "ok"})

    register_all(mcp, ctx)
    return mcp


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
