"""Logging configuration + redaction tests."""

from __future__ import annotations

import json
import logging

from mcp_phish.logging_setup import JsonFormatter, _scrub, configure_logging


def test_scrub_redacts_known_keys() -> None:
    data = {"phishnet_api_key": "secret", "ok": "value", "nested": {"apikey": "x"}}
    scrubbed = _scrub(data)
    assert scrubbed["phishnet_api_key"] == "[REDACTED]"
    assert scrubbed["nested"]["apikey"] == "[REDACTED]"
    assert scrubbed["ok"] == "value"


def test_scrub_handles_lists() -> None:
    out = _scrub([{"password": "x"}, "plain"])
    assert out[0]["password"] == "[REDACTED]"
    assert out[1] == "plain"


def test_json_formatter_emits_iso_timestamp() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.api_key = "topsecret"  # type: ignore[attr-defined]
    formatted = JsonFormatter().format(record)
    payload = json.loads(formatted)
    assert payload["msg"] == "hello"
    assert "T" in payload["ts"]
    assert payload["extra"]["api_key"] == "[REDACTED]"


def test_configure_logging_text_mode_is_idempotent() -> None:
    configure_logging(level="WARNING", fmt="text")
    configure_logging(level="WARNING", fmt="text")
    handlers = logging.getLogger().handlers
    assert len(handlers) == 1
