"""Config validation tests."""

from __future__ import annotations

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
