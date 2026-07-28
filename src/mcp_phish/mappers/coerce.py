"""Scalar coercion for untrusted upstream values.

Both upstream APIs and the vault hand back fields that are sometimes a string,
sometimes a number, sometimes ``None``, and occasionally an empty string where
a number is documented. Every mapper funnels those through the same three
functions so a malformed field degrades to a default instead of raising out of
a tool.

This is deliberately three functions with one job, not a utilities dumping
ground. Anything that needs to know about a specific upstream's field names
belongs in that upstream's mapper module.
"""

from __future__ import annotations

from typing import Any

__all__ = ["safe_float", "safe_int", "safe_str"]


def safe_str(value: Any) -> str:
    """Return ``value`` as a string, mapping ``None`` to ``""``."""
    return "" if value is None else str(value)


def safe_int(value: Any, default: int = 0) -> int:
    """Return ``value`` as an int, falling back to ``default`` when uncoercible."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_float(value: Any) -> float | None:
    """Return ``value`` as a float, or ``None`` when absent or uncoercible."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
