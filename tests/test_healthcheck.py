"""Tests for the Docker HEALTHCHECK script."""

from __future__ import annotations

import urllib.error
from unittest.mock import patch

import pytest

from mcp_phish.healthcheck import _HEALTHY_NON_OK_CODES, check


def test_healthy_codes_set_includes_405_406() -> None:
    assert 405 in _HEALTHY_NON_OK_CODES
    assert 406 in _HEALTHY_NON_OK_CODES


def test_check_returns_zero_on_405(monkeypatch: pytest.MonkeyPatch) -> None:
    """FastMCP returns 405 to bare GETs on /mcp; that's healthy."""
    err = urllib.error.HTTPError(
        "http://localhost:3705/mcp",
        405,
        "Method Not Allowed",
        hdrs=None,
        fp=None,  # type: ignore[arg-type]
    )
    with patch("urllib.request.urlopen", side_effect=err):
        assert check() == 0


def test_check_returns_one_on_unhealthy_500(monkeypatch: pytest.MonkeyPatch) -> None:
    err = urllib.error.HTTPError(
        "http://localhost:3705/mcp",
        500,
        "Internal",
        hdrs=None,
        fp=None,  # type: ignore[arg-type]
    )
    with patch("urllib.request.urlopen", side_effect=err):
        assert check() == 1


def test_check_returns_one_on_connect_error() -> None:
    with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
        assert check() == 1
