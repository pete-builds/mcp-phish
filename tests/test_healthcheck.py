"""Tests for the Docker healthcheck script and the ``/health`` route."""

from __future__ import annotations

import urllib.error
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from mcp_phish.config import Settings
from mcp_phish.healthcheck import check
from mcp_phish.server import build_server


def test_healthcheck_ok_on_200() -> None:
    resp = MagicMock()
    resp.status = 200
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False

    with patch("urllib.request.urlopen", return_value=resp):
        assert check() == 0


def test_healthcheck_fails_on_non_200() -> None:
    resp = MagicMock()
    resp.status = 503
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False

    with patch("urllib.request.urlopen", return_value=resp):
        assert check() == 1


def test_healthcheck_fails_on_http_error() -> None:
    err = urllib.error.HTTPError("u", 500, "ise", None, None)  # type: ignore[arg-type]
    with patch("urllib.request.urlopen", side_effect=err):
        assert check() == 1


def test_healthcheck_fails_on_404() -> None:
    err = urllib.error.HTTPError("u", 404, "not found", None, None)  # type: ignore[arg-type]
    with patch("urllib.request.urlopen", side_effect=err):
        assert check() == 1


def test_healthcheck_fails_on_connection_error() -> None:
    with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
        assert check() == 1


def test_health_route_returns_ok_without_touching_mcp_transport() -> None:
    """``/health`` returns HTTP 200 and never mints an MCP transport session.

    The Docker HEALTHCHECK gates on the 200 status code (see
    ``healthcheck.check``); the body is intentionally minimal.
    """
    settings = Settings()
    app = build_server(settings).http_app()
    with TestClient(app) as client:
        resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"status": "ok"}
