"""Config validation tests."""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

import pytest
from pydantic import ValidationError

from mcp_phish.config import Settings


def test_stub_mode_is_default_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STUB_MODE", raising=False)
    monkeypatch.delenv("PHISHNET_API_KEY", raising=False)
    settings = Settings()
    assert settings.stub_mode is True


def test_real_mode_requires_phishnet_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHISHNET_API_KEY", raising=False)
    with pytest.raises(ValidationError) as exc:
        Settings(stub_mode=False)
    assert "PHISHNET_API_KEY" in str(exc.value)


def test_real_mode_with_key_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHISHNET_API_KEY", raising=False)
    s = Settings(stub_mode=False, phishnet_api_key="abc123")
    assert s.phishnet_api_key == "abc123"


def test_throttle_rps_lower_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("THROTTLE_PHISHNET_RPS", raising=False)
    with pytest.raises(ValidationError):
        Settings(stub_mode=True, throttle_phishnet_rps=0)


def test_safe_repr_redacts_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHISHNET_API_KEY", raising=False)
    settings = Settings(stub_mode=False, phishnet_api_key="topsecret")
    repr_dict = settings.safe_repr()
    assert repr_dict["phishnet_api_key_set"] is True
    # The actual key value should not appear anywhere in the repr.
    assert "topsecret" not in str(repr_dict)


def test_cache_ttl_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings(stub_mode=True, cache_ttl_seconds=10)
    with pytest.raises(ValidationError):
        Settings(stub_mode=True, cache_ttl_seconds=10**9)


def test_mcp_port_range() -> None:
    with pytest.raises(ValidationError):
        Settings(stub_mode=True, mcp_port=0)
    with pytest.raises(ValidationError):
        Settings(stub_mode=True, mcp_port=70000)


def test_pg_dsn_percent_encodes_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """asyncpg parses the DSN as a URL, so ``@ / : # ?`` in a password must be
    encoded or the host, password and database all land in the wrong fields."""
    for var in ("PG_USER", "PG_PASSWORD", "PG_HOST", "PG_PORT", "PG_DB"):
        monkeypatch.delenv(var, raising=False)
    settings = Settings(stub_mode=True, pg_user="ph@sh", pg_password="p@ss/w:rd#1?")

    parts = urlsplit(settings.pg_dsn)

    assert parts.scheme == "postgresql"
    assert parts.hostname == "postgres"
    assert parts.port == 5432
    assert parts.path == "/phish"
    assert parts.username is not None and unquote(parts.username) == "ph@sh"
    assert parts.password is not None and unquote(parts.password) == "p@ss/w:rd#1?"
    # The raw secret never appears unencoded in the DSN.
    assert "p@ss/w:rd#1?" not in settings.pg_dsn
