"""Async client for api.phish.net v5.

The phish.net v5 API is an apikey-as-query-parameter REST API returning
JSON-wrapped responses. The standard envelope is:

```json
{
    "error": false,
    "error_message": "",
    "response": {
        "count": <int>,
        "data":  [<rows>] | <row>
    }
}
```

This client unwraps that envelope and returns ``response.data``. Network
errors raise :class:`PhishNetError`; the server tools catch and translate.

Endpoints we use (read-only):

* ``/shows/showdate/<YYYY-MM-DD>.json`` — show + setlist for a given date
* ``/shows/showid/<id>.json`` — show by id
* ``/shows/query.json`` — search shows (year/venue/city/state/country)
* ``/setlists/showdate/<YYYY-MM-DD>.json`` — setlist rows for a date
* ``/songs.json`` — list of every song (paginated)
* ``/songs/slug/<slug>.json`` — song by slug
* ``/songs/query.json`` — search songs by title fragment
* ``/performances/song/<slug>.json`` — every performance of a song
* ``/jamcharts.json`` — jam-chart entries (optionally filtered)
* ``/reviews/showdate/<YYYY-MM-DD>.json`` — reviews for a date
* ``/reviews/showid/<id>.json`` — reviews by show id

The API surface is intentionally narrow. New endpoints get added here only
when a tool needs them.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from mcp_phish.throttle import TokenBucket

logger = logging.getLogger("mcp_phish.client.phishnet")


class PhishNetError(RuntimeError):
    """Raised on any non-2xx response or transport failure."""


class PhishNetClient:
    """Thin async wrapper around api.phish.net v5.

    Args:
        api_key: API key (free at https://api.phish.net/keys/). Sent as the
            ``apikey`` query parameter on every call.
        base_url: Override for testing. Defaults to ``https://api.phish.net/v5``.
        throttle: Per-instance token bucket. ``acquire()`` is awaited before
            every upstream call.
        timeout: Per-request timeout in seconds.
    """

    def __init__(
        self,
        api_key: str,
        throttle: TokenBucket,
        base_url: str = "https://api.phish.net/v5",
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._throttle = throttle
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Accept": "application/json"},
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET ``<base>/<path>`` with apikey injected. Returns ``response.data``.

        Retries once on a transient connection error. Anything else surfaces
        as :class:`PhishNetError` for the caller.
        """
        await self._throttle.acquire()
        merged: dict[str, Any] = {"apikey": self.api_key}
        if params:
            merged.update(params)
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                resp = await self._client.get(url, params=merged)
            except (httpx.ConnectError, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                if attempt == 0:
                    logger.warning(
                        "phish.net connection error, retrying once",
                        extra={"path": path, "error": str(exc)},
                    )
                    continue
                raise PhishNetError(f"phish.net connection failed: {exc}") from exc
            except httpx.HTTPError as exc:
                raise PhishNetError(f"phish.net transport error: {exc}") from exc

            if resp.status_code >= 400:
                raise PhishNetError(
                    f"phish.net GET {path} returned {resp.status_code}: {resp.text[:300]}"
                )
            try:
                body = resp.json()
            except ValueError as exc:
                raise PhishNetError(f"phish.net returned invalid JSON: {exc}") from exc

            # v5 envelope. Older endpoints sometimes return `data` at the top
            # level; tolerate both.
            if isinstance(body, dict):
                if body.get("error"):
                    msg = body.get("error_message") or "unknown"
                    raise PhishNetError(f"phish.net error: {msg}")
                if "response" in body and isinstance(body["response"], dict):
                    return body["response"].get("data")
                if "data" in body:
                    return body["data"]
            return body

        raise PhishNetError(  # pragma: no cover — defensive
            f"phish.net request exhausted retries: {last_exc}"
        )

    # ------------------------------------------------------------------
    # Public methods (one per server tool that needs a real call)
    # ------------------------------------------------------------------

    async def get_show_by_date(self, date: str) -> Any:
        return await self._get(f"shows/showdate/{date}.json")

    async def get_show_by_id(self, show_id: str) -> Any:
        return await self._get(f"shows/showid/{show_id}.json")

    async def search_shows(self, params: dict[str, Any]) -> Any:
        return await self._get("shows/query.json", params=params)

    async def get_setlist_by_date(self, date: str) -> Any:
        return await self._get(f"setlists/showdate/{date}.json")

    async def list_songs(self, params: dict[str, Any] | None = None) -> Any:
        return await self._get("songs.json", params=params)

    async def get_song_by_slug(self, slug: str) -> Any:
        return await self._get(f"songs/slug/{slug}.json")

    async def search_songs(self, params: dict[str, Any]) -> Any:
        return await self._get("songs/query.json", params=params)

    async def song_performances(self, slug: str, params: dict[str, Any] | None = None) -> Any:
        return await self._get(f"performances/song/{slug}.json", params=params)

    async def jam_chart(self, params: dict[str, Any] | None = None) -> Any:
        return await self._get("jamcharts.json", params=params)

    async def reviews_by_date(self, date: str) -> Any:
        return await self._get(f"reviews/showdate/{date}.json")

    async def reviews_by_id(self, show_id: str) -> Any:
        return await self._get(f"reviews/showid/{show_id}.json")
