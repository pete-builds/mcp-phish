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


def _post_initialize(port: int, host_header: str) -> tuple[int, str]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(
            "POST",
            "/mcp",
            body=json.dumps(INITIALIZE),
            headers={
                "Host": host_header,
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        resp = conn.getresponse()
        # Enough to see the JSON-RPC result line; an SSE body may stay open.
        return resp.status, resp.read1(4096).decode(errors="replace")
    finally:
        conn.close()


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
