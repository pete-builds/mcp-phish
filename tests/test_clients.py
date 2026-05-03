"""HTTP-level tests for the real-mode clients (respx-mocked)."""

from __future__ import annotations

import httpx
import pytest
import respx

from mcp_phish.clients.phishin import PhishInClient, PhishInError
from mcp_phish.clients.phishnet import PhishNetClient, PhishNetError
from mcp_phish.throttle import TokenBucket

# ---------------------------------------------------------------------------
# phish.net
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_phishnet_unwraps_v5_envelope() -> None:
    route = respx.get("https://api.phish.test/v5/shows/showdate/1995-12-30.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "error": False,
                "error_message": "",
                "response": {
                    "count": 1,
                    "data": [{"showid": "1252691618", "showdate": "1995-12-30", "venue": "MSG"}],
                },
            },
        )
    )
    client = PhishNetClient(
        api_key="k",
        throttle=TokenBucket(rps=50),
        base_url="https://api.phish.test/v5",
    )
    try:
        rows = await client.get_show_by_date("1995-12-30")
        assert isinstance(rows, list) and rows[0]["venue"] == "MSG"
        assert route.called
        # apikey query param must be present on the call.
        called_url = route.calls[0].request.url
        assert "apikey=k" in str(called_url)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_phishnet_handles_flat_envelope() -> None:
    """Some endpoints / older mirrors return {data: [...]} with no wrapper."""
    respx.get("https://api.phish.test/v5/songs.json").mock(
        return_value=httpx.Response(200, json={"data": [{"slug": "ghost", "title": "Ghost"}]})
    )
    client = PhishNetClient(
        api_key="k",
        throttle=TokenBucket(rps=50),
        base_url="https://api.phish.test/v5",
    )
    try:
        rows = await client.list_songs()
        assert rows[0]["slug"] == "ghost"
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_phishnet_raises_on_envelope_error() -> None:
    respx.get("https://api.phish.test/v5/shows/showdate/2099-01-01.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "error": True,
                "error_message": "Invalid date",
                "response": {"count": 0, "data": []},
            },
        )
    )
    client = PhishNetClient(
        api_key="k",
        throttle=TokenBucket(rps=50),
        base_url="https://api.phish.test/v5",
    )
    try:
        with pytest.raises(PhishNetError) as exc:
            await client.get_show_by_date("2099-01-01")
        assert "Invalid date" in str(exc.value)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_phishnet_raises_on_5xx() -> None:
    respx.get("https://api.phish.test/v5/songs.json").mock(
        return_value=httpx.Response(503, text="upstream down")
    )
    client = PhishNetClient(
        api_key="k",
        throttle=TokenBucket(rps=50),
        base_url="https://api.phish.test/v5",
    )
    try:
        with pytest.raises(PhishNetError):
            await client.list_songs()
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_phishnet_retries_once_on_connect_error() -> None:
    side_effects = [
        httpx.ConnectError("nope"),
        httpx.Response(200, json={"response": {"count": 0, "data": []}}),
    ]
    route = respx.get("https://api.phish.test/v5/songs.json").mock(side_effect=side_effects)
    client = PhishNetClient(
        api_key="k",
        throttle=TokenBucket(rps=50),
        base_url="https://api.phish.test/v5",
    )
    try:
        rows = await client.list_songs()
        assert rows == []
        assert route.call_count == 2
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# phish.in
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_phishin_get_show_passthrough() -> None:
    payload = {"id": 412, "date": "1995-12-30", "tracks": []}
    respx.get("https://phish.test/api/v2/shows/1995-12-30").mock(
        return_value=httpx.Response(200, json=payload)
    )
    client = PhishInClient(
        throttle=TokenBucket(rps=50),
        api_key="",
        base_url="https://phish.test/api/v2",
    )
    try:
        body = await client.get_show("1995-12-30")
        assert body == payload
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_phishin_404_returns_none() -> None:
    respx.get("https://phish.test/api/v2/shows/0000-00-00").mock(
        return_value=httpx.Response(404, text="not found")
    )
    client = PhishInClient(
        throttle=TokenBucket(rps=50),
        api_key="",
        base_url="https://phish.test/api/v2",
    )
    try:
        body = await client.get_show("0000-00-00")
        assert body is None
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_phishin_5xx_raises() -> None:
    respx.get("https://phish.test/api/v2/tracks/1").mock(
        return_value=httpx.Response(500, text="boom")
    )
    client = PhishInClient(
        throttle=TokenBucket(rps=50),
        api_key="",
        base_url="https://phish.test/api/v2",
    )
    try:
        with pytest.raises(PhishInError):
            await client.get_track(1)
    finally:
        await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_phishin_sends_bearer_token_when_key_present() -> None:
    route = respx.get("https://phish.test/api/v2/tracks/1").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    client = PhishInClient(
        throttle=TokenBucket(rps=50),
        api_key="topkey",
        base_url="https://phish.test/api/v2",
    )
    try:
        await client.get_track(1)
        auth = route.calls[0].request.headers.get("Authorization")
        assert auth == "Bearer topkey"
    finally:
        await client.aclose()
