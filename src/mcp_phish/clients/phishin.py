"""Async client for phish.in v2.

phish.in v2 returns paginated index endpoints with the shape::

    {"<resource>": [...], "total_pages": N, "current_page": M, "total_entries": T}

and singleton endpoints as a flat object. Authentication is anonymous-friendly;
an API key (sent as ``Authorization: Bearer <key>``) just raises rate caps.

This client returns raw upstream payloads. Projection into the public Pydantic
contract happens in ``server.py``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from mcp_phish.throttle import TokenBucket

logger = logging.getLogger("mcp_phish.client.phishin")


class PhishInError(RuntimeError):
    """Raised on any non-2xx response or transport failure."""


class PhishInClient:
    """Thin async wrapper around phish.in v2.

    Args:
        api_key: Optional API key. Empty string means anonymous (lower rate
            caps but otherwise full read access).
        throttle: Per-instance token bucket.
        base_url: Override for testing.
        timeout: Per-request timeout in seconds.
    """

    def __init__(
        self,
        throttle: TokenBucket,
        api_key: str = "",
        base_url: str = "https://phish.in/api/v2",
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._throttle = throttle
        headers: dict[str, str] = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers=headers,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        await self._throttle.acquire()
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                resp = await self._client.get(url, params=params)
            except (httpx.ConnectError, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                if attempt == 0:
                    logger.warning(
                        "phish.in connection error, retrying once",
                        extra={"path": path, "error": str(exc)},
                    )
                    continue
                raise PhishInError(f"phish.in connection failed: {exc}") from exc
            except httpx.HTTPError as exc:
                raise PhishInError(f"phish.in transport error: {exc}") from exc

            if resp.status_code == 404:
                # Surface as an empty result so callers can translate to NOT_FOUND.
                return None
            if resp.status_code >= 400:
                raise PhishInError(
                    f"phish.in GET {path} returned {resp.status_code}: {resp.text[:300]}"
                )
            try:
                return resp.json()
            except ValueError as exc:
                raise PhishInError(f"phish.in returned invalid JSON: {exc}") from exc

        raise PhishInError(  # pragma: no cover - defensive
            f"phish.in request exhausted retries: {last_exc}"
        )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def get_show(self, date_or_id: str) -> Any:
        """Lookup is by date (YYYY-MM-DD) or numeric id; both share /shows/<key>."""
        return await self._get(f"shows/{date_or_id}")

    async def get_track(self, track_id: int) -> Any:
        return await self._get(f"tracks/{track_id}")

    async def search_tracks(self, params: dict[str, Any]) -> Any:
        return await self._get("tracks", params=params)
