"""The wire contract: every tool return value is serialized through here.

Two shapes only, per the Standard Error Contract:

* success — ``{"data": <payload>}``
* failure — ``{"error": str, "code": str, "details": {...}}``

Keeping both writers in one module means the envelope can only ever change
in one place, and a reader can confirm the contract without opening a tool.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

__all__ = ["err", "ok", "to_jsonable"]


def to_jsonable(obj: Any) -> Any:
    """Recursively convert pydantic models / sequences into JSON-friendly types."""
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [to_jsonable(item) for item in obj]
    return obj


def ok(data: Any) -> str:
    """Serialize a ``data`` payload. Pydantic models flatten via ``model_dump``."""
    return json.dumps({"data": to_jsonable(data)}, indent=2, default=str)


def err(message: str, code: str, **details: Any) -> str:
    """Serialize the standard failure shape."""
    payload: dict[str, Any] = {"error": message, "code": code}
    if details:
        payload["details"] = details
    return json.dumps(payload, indent=2, default=str)
