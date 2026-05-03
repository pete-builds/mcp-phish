"""Upstream API clients (live + stub) for mcp-phish."""

from mcp_phish.clients.phishin import PhishInClient, PhishInError
from mcp_phish.clients.phishnet import PhishNetClient, PhishNetError
from mcp_phish.clients.stubs import StubPhishInClient, StubPhishNetClient

__all__ = [
    "PhishInClient",
    "PhishInError",
    "PhishNetClient",
    "PhishNetError",
    "StubPhishInClient",
    "StubPhishNetClient",
]
