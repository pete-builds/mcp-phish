"""Response envelope helpers (Standard Error Contract).

Every MCP tool returns a JSON string in one of two shapes:

* success → ``{"data": <payload>}``
* failure → ``{"error": <message>, "code": <enum>, "details": {...}}``

These helpers are pure and import-light so every tool module can share them
without dragging in the server wiring.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel


def _to_jsonable(obj: Any) -> Any:
    """Recursively convert pydantic models / sequences into JSON-friendly types."""
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_to_jsonable(item) for item in obj]
    return obj


def _ok(data: Any) -> str:
    """Serialize a ``data`` payload. Pydantic models flatten via ``model_dump``."""
    return json.dumps({"data": _to_jsonable(data)}, indent=2, default=str)


def _err(message: str, code: str, **details: Any) -> str:
    """Serialize the standard failure shape."""
    payload: dict[str, Any] = {"error": message, "code": code}
    if details:
        payload["details"] = details
    return json.dumps(payload, indent=2, default=str)


__all__ = ["_err", "_ok", "_to_jsonable"]
