"""The server answers a LAN client, not just loopback.

This server is reached at http://192.168.86.20:3705, so every real request
carries a non-loopback ``Host`` header. MCP SDK 2 changed the defaults that
decide whether such a request is served: a server built the SDK's way binds
127.0.0.1 and switches on DNS-rebinding protection, which answers any other
Host with HTTP 421. FastMCP 4 carries the same guard behind its own setting.
Neither failure shows up anywhere else: every in-process test passes, and the
Docker healthcheck probes localhost, so the container reports healthy while
every LAN client is refused.

So this boots the real entrypoint (``python -m mcp_phish.server``, the
Dockerfile's ENTRYPOINT) on a free port and sends an MCP ``initialize`` with
the Host header a LAN client sends. The request goes over loopback, which is
the strict case: a guard in "auto" mode validates Host whenever the socket's
local address is loopback, so passing here also covers a LAN client arriving
on the container's own address.

The control boots the same entrypoint the loopback way and requires a 421.
It runs on every CI run, so a probe that silently stopped detecting the 421
(wrong header, wrong path, a server that never started) fails here instead of
reading as a pass.

The same harness checks session reaping. FastMCP 4 hands the SDK's session
manager its own idle-timeout setting, which defaults to never, so abandoned
sessions would pile up silently. ``main()`` restores 30 minutes; the tests
read the value off the live session manager and prove an operator override
reaches a running server.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import fastmcp
import pytest
import uvicorn

from mcp_phish import server as server_module

LAN_HOST = "192.168.86.20:3705"

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "lan-host-test", "version": "0"},
    },
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextmanager
def _server(tmp_path: Path, **overrides: str) -> Iterator[int]:
    port = _free_port()
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("FASTMCP_", "MCP_", "PHISH", "PG_", "VAULT_"))
    }
    env.update(
        STUB_MODE="true",
        MCP_HOST="0.0.0.0",
        MCP_PORT=str(port),
        CACHE_DB_PATH=str(tmp_path / "cache.db"),
        LOG_FORMAT="text",
        FASTMCP_CHECK_FOR_UPDATES="off",
        FASTMCP_SHOW_SERVER_BANNER="false",
    )
    env.update(overrides)
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_phish.server"],
        cwd=tmp_path,  # keep a developer's .env out of it
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            if proc.poll() is not None:
                out = proc.stdout.read().decode() if proc.stdout else ""
                raise AssertionError(f"server exited early ({proc.returncode}):\n{out}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise AssertionError("server never started listening") from None
                time.sleep(0.1)
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _post(
    port: int, host_header: str, body: dict[str, object], session_id: str | None = None
) -> tuple[int, str, str | None]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {
        "Host": host_header,
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    try:
        conn.request("POST", "/mcp", body=json.dumps(body), headers=headers)
        resp = conn.getresponse()
        # Enough to see the JSON-RPC result line; an SSE body may stay open.
        text = resp.read1(4096).decode(errors="replace")
        return resp.status, text, resp.getheader("mcp-session-id")
    finally:
        conn.close()


def _post_initialize(port: int, host_header: str) -> tuple[int, str]:
    status, body, _ = _post(port, host_header, INITIALIZE)
    return status, body


def test_lan_host_header_is_served(tmp_path: Path) -> None:
    with _server(tmp_path) as port:
        status, body = _post_initialize(port, LAN_HOST)
    assert status != 421, f"LAN Host refused as misdirected: {body!r}"
    assert status == 200, f"initialize failed with {status}: {body!r}"
    assert '"serverInfo"' in body, body


def test_control_loopback_build_refuses_lan_host(tmp_path: Path) -> None:
    """Positive control: built the loopback way, the same probe must see 421."""
    overrides = {"MCP_HOST": "127.0.0.1", "FASTMCP_HTTP_HOST_ORIGIN_PROTECTION": "auto"}
    with _server(tmp_path, **overrides) as port:
        status, body = _post_initialize(port, LAN_HOST)
        loopback_status, _ = _post_initialize(port, f"127.0.0.1:{port}")
    assert status == 421, f"control expected 421, got {status}: {body!r}"
    # The guard, not a dead server: the same server still answers its own name.
    assert loopback_status == 200


# --- Session reaping -------------------------------------------------------


@pytest.fixture
def main_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run ``main()`` in-process: stub mode, no banner, no PyPI, settings restored."""
    for var in [k for k in os.environ if k.startswith(("FASTMCP_", "MCP_"))]:
        monkeypatch.delenv(var)
    monkeypatch.chdir(tmp_path)  # keep a developer's .env out of it
    monkeypatch.setenv("STUB_MODE", "true")
    monkeypatch.setenv("CACHE_DB_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setattr(server_module, "configure_logging", lambda **_: None)
    monkeypatch.setattr(fastmcp.settings, "show_server_banner", False)
    monkeypatch.setattr(fastmcp.settings, "check_for_updates", "off")
    # FastMCP 4's own default. monkeypatch puts it back after main() changes it.
    monkeypatch.setattr(fastmcp.settings, "http_session_idle_timeout", None)


def _live_session_idle_timeout(monkeypatch: pytest.MonkeyPatch) -> float | None:
    """Run the real ``main()`` and read the timeout off the live session manager.

    uvicorn's ``serve()`` is swapped for a probe that starts the app's lifespan
    (where FastMCP builds the session manager) and reads it, then returns.
    """
    seen: dict[str, float | None] = {}

    async def probe(self: uvicorn.Server, sockets: object = None) -> None:
        app = self.config.app
        async with app.router.lifespan_context(app):
            for route in app.routes:
                manager = getattr(getattr(route, "endpoint", None), "session_manager", None)
                if manager is not None:
                    seen["timeout"] = manager.session_idle_timeout

    monkeypatch.setattr(uvicorn.Server, "serve", probe)
    server_module.main()
    assert "timeout" in seen, "no streamable-HTTP session manager found on the app"
    return seen["timeout"]


@pytest.mark.usefixtures("main_env")
def test_main_reaps_idle_sessions_after_30_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _live_session_idle_timeout(monkeypatch) == 1800


@pytest.mark.usefixtures("main_env")
def test_operator_idle_timeout_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    # FastMCP reads the env var into its settings at import; mirror both halves.
    monkeypatch.setenv("FASTMCP_HTTP_SESSION_IDLE_TIMEOUT", "120")
    monkeypatch.setattr(fastmcp.settings, "http_session_idle_timeout", 120.0)
    assert _live_session_idle_timeout(monkeypatch) == 120


def _session_survives(port: int, wait: float) -> int:
    """Open a session, go idle for ``wait`` seconds, then use it. Returns the status."""
    status, body, session_id = _post(port, LAN_HOST, INITIALIZE)
    assert status == 200 and session_id, (status, body)
    note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert _post(port, LAN_HOST, note, session_id)[0] == 202
    time.sleep(wait)
    listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    return _post(port, LAN_HOST, listing, session_id)[0]


def test_idle_timeout_override_reaches_the_running_server(tmp_path: Path) -> None:
    """A 1 second override reaps a real session; the default keeps it (control)."""
    with _server(tmp_path, FASTMCP_HTTP_SESSION_IDLE_TIMEOUT="1") as port:
        assert _session_survives(port, wait=3) == 404
    with _server(tmp_path) as port:
        assert _session_survives(port, wait=3) == 200
