"""Structured logging configuration for mcp-phish.

In production we emit JSON via stdlib ``logging`` with a custom formatter so
log aggregators can parse each record without regex hacks. ``log_format=text``
falls back to a plain human-readable format for local development.

API keys are kept out of logs by two separate mechanisms, and it is worth being
precise about what each one does, because the first sentence here used to claim
more than the code delivered.

1. The ``httpx`` and ``httpcore`` loggers are pinned to WARNING. httpx logs every
   request URL at INFO, and phish.net takes its API key as a query parameter, so
   without this the key was written to stdout on every call.
2. The formatter scrubs a small set of well-known sensitive keys out of the
   structured ``extra`` dict, defensively, in case caller code drops one there.

Note the limit of (2): it does **not** scrub the formatted message text. A secret
interpolated into a log message string is emitted verbatim. Keep secrets out of
message bodies.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "phishnet_api_key",
        "phishin_api_key",
        "x-api-key",
        "x_api_key",
        "authorization",
        "password",
    }
)

_RESERVED_LOGRECORD_FIELDS: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


def _scrub(value: Any) -> Any:
    """Recursively replace sensitive values with ``[REDACTED]``."""
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if k.lower() in _SENSITIVE_KEYS else _scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    """Serialise each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        extras = {
            key: ("[REDACTED]" if key.lower() in _SENSITIVE_KEYS else _scrub(value))
            for key, value in record.__dict__.items()
            if key not in _RESERVED_LOGRECORD_FIELDS and not key.startswith("_")
        }
        if extras:
            payload["extra"] = extras
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Configure the root logger. Idempotent — safe to call multiple times."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream=sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        )
    root.addHandler(handler)

    # phish.net requires its API key as a query parameter -- the upstream gives us
    # no header form -- and httpx logs every completed request at INFO including
    # the full URL. With the root logger at its default INFO, that wrote
    # apikey=<real key> to stdout on every single call, and into the rotated
    # docker json-file logs behind it.
    #
    # The scrubber above cannot catch it: it walks the structured `extra` dict and
    # never touches the formatted message text, which is where httpx puts the URL.
    # Pinning the logger is the fix, and it is the same one phish-vault already
    # carries (src/phish_vault/logging_setup.py:126-129) against the same API for
    # the same reason.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
