"""Every tool declares itself read-only, and that claim is checked.

Sixteen tools, every one a lookup, and not one writes anything anywhere. That
is worth declaring rather than leaving to be inferred: an unannotated read-only
server and an unannotated server full of delete tools are indistinguishable in
the manifest, so a client trying to be careful has to be careful about
everything -- which in practice means being careful about nothing.

Saying "these sixteen are safe" is what makes "that one is not", elsewhere in
the fleet, mean something.

The coverage assertion keys off TOOL_INDEX rather than a list written here.
That file is already the single source of truth for what this server exposes,
with tests/test_tool_index.py asserting it matches what FastMCP registered, so
a tool added without an annotation fails here without anyone having to
remember to update this file too.
"""

from __future__ import annotations

import pytest

from mcp_phish.config import Settings
from mcp_phish.server import build_server
from mcp_phish.tools import ALL_TOOL_NAMES


@pytest.fixture
async def tools(stub_settings: Settings):
    """The live manifest, not the source. What a client would receive."""
    server = build_server(stub_settings)
    return {t.name: t for t in await server.list_tools()}


async def test_the_manifest_matches_the_tool_index(tools):
    """Guards the guard: an empty manifest would pass everything below."""
    assert set(tools) == set(ALL_TOOL_NAMES)


async def test_every_tool_is_annotated(tools):
    assert sorted(n for n, t in tools.items() if t.annotations is None) == []


async def test_every_tool_is_read_only(tools):
    """The whole surface. A write tool added later fails here first.

    The failure is a prompt to classify the new tool deliberately, not an
    obstacle to adding one.
    """
    assert sorted(n for n, t in tools.items() if not t.annotations.readOnlyHint) == []


async def test_nothing_claims_to_be_destructive(tools):
    assert sorted(n for n, t in tools.items() if t.annotations.destructiveHint) == []


async def test_open_world_and_idempotent_together(tools):
    """Both at once, deliberately.

    Every read reaches either the phish.net API or the vault, which is a
    Postgres server on another host. So an answer can differ between two
    identical calls because the world moved, which is a different thing from
    the call having changed it.
    """
    assert sorted(n for n, t in tools.items() if not t.annotations.openWorldHint) == []
    assert sorted(n for n, t in tools.items() if not t.annotations.idempotentHint) == []
