"""Audit decorator for MCP tool functions.

Mirrors the ``@audited("<tool_name>")`` pattern from mcp-unifi: every tool is
wrapped at the tool layer so each invocation emits one structured debug record
(tool name, kwargs, success/failure, wall-clock latency). The decorator lives at
the tool layer, not the client layer, so the captured envelope reflects
user-facing tool intent.

Unlike mcp-unifi (which ships a full pluggable audit-sink subsystem), mcp-phish
emits through the standard ``logging`` module at DEBUG level. This keeps the
public behavior identical to the pre-refactor server (no new prod-visible side
effects, no new env config) while giving the same per-tool observability hook
and the same module structure. If a richer sink is ever needed, swap the
``logger.debug`` calls for an audit-log emit without touching any tool body.

Design notes
------------
* Tool bodies are ``async def`` and return JSON strings (the payload Claude
  sees). The decorator preserves the original signature via ``functools.wraps``
  so FastMCP's schema introspection sees the same parameters it would for the
  bare function.
* On exception the decorator logs ``success=False`` with the error string and
  re-raises. The audit log line never swallows the exception.
* Latency is wall-clock milliseconds measured around the wrapped coroutine.
* Sensitive args are not a concern here: phish tools take only public
  identifiers (dates, slugs, ids, limits). Nothing secret is ever an argument.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

logger = logging.getLogger("mcp_phish.audit")

P = ParamSpec("P")
R = TypeVar("R")


def audited(
    tool_name: str,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Wrap an async tool function so every call emits a debug audit record.

    Args:
        tool_name: The MCP tool name as registered with FastMCP. Must match the
            function name (or ``@mcp.tool(name=...)`` override) so audit log
            lines line up with what a caller actually invoked.

    The wrapped function preserves its original signature, so FastMCP's schema
    introspection sees the same parameters it would for the bare function.
    """

    def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            # Tools are invoked with kwargs from FastMCP's JSON dispatch.
            # Defensive: capture stray positionals so they aren't dropped.
            audit_args: dict[str, Any] = dict(kwargs)
            if args:
                audit_args["_positional"] = list(args)

            start = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                latency_ms = (time.perf_counter() - start) * 1000.0
                logger.debug(
                    "tool call failed",
                    extra={
                        "tool": tool_name,
                        "args": audit_args,
                        "success": False,
                        "latency_ms": latency_ms,
                        "error": str(exc),
                    },
                )
                raise

            latency_ms = (time.perf_counter() - start) * 1000.0
            logger.debug(
                "tool call",
                extra={
                    "tool": tool_name,
                    "args": audit_args,
                    "success": True,
                    "latency_ms": latency_ms,
                },
            )
            return result

        return wrapper

    return decorator


__all__ = ["audited"]
