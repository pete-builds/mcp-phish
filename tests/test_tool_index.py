"""TOOL_INDEX must equal what FastMCP actually registered.

This is the regression guard for tool-surface drift. Before the server was
split into modules, "how many tools does this expose?" was answered by
counting decorators and the answer went stale in the README. Now the answer
lives in ``mcp_phish.tools.TOOL_INDEX`` and this test proves it is true.

A tool added, renamed, or deleted without updating TOOL_INDEX fails here.
"""

from __future__ import annotations

from mcp_phish.config import Settings
from mcp_phish.server import build_server
from mcp_phish.tools import ALL_TOOL_NAMES, TOOL_INDEX

EXPECTED_TOOL_COUNT = 16


async def test_tool_index_matches_registered_tools(stub_settings: Settings) -> None:
    """Every name in TOOL_INDEX is registered, and nothing else is."""
    server = build_server(stub_settings)
    registered = {tool.name for tool in await server.list_tools()}

    assert registered == set(ALL_TOOL_NAMES), (
        f"tool surface drifted.\n"
        f"  registered but not in TOOL_INDEX: {sorted(registered - set(ALL_TOOL_NAMES))}\n"
        f"  in TOOL_INDEX but not registered: {sorted(set(ALL_TOOL_NAMES) - registered)}"
    )


async def test_tool_count_is_pinned(stub_settings: Settings) -> None:
    """The tool count is pinned so a surface change is a deliberate edit."""
    server = build_server(stub_settings)
    registered = {tool.name for tool in await server.list_tools()}

    assert len(registered) == EXPECTED_TOOL_COUNT
    assert len(ALL_TOOL_NAMES) == EXPECTED_TOOL_COUNT


def test_tool_index_has_no_duplicate_names() -> None:
    """A name may only be claimed by one module."""
    flat = [name for names in TOOL_INDEX.values() for name in names]
    assert len(flat) == len(set(flat)), "duplicate tool name across TOOL_INDEX modules"


def test_tool_index_modules_are_importable() -> None:
    """Every TOOL_INDEX key names a real module that exposes ``register``."""
    import importlib

    for module_path in TOOL_INDEX:
        module = importlib.import_module(module_path)
        assert callable(module.register), f"{module_path} has no register()"
