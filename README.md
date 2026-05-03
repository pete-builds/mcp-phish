# mcp-phish

[![CI](https://github.com/pete-builds/mcp-phish/actions/workflows/ci.yml/badge.svg)](https://github.com/pete-builds/mcp-phish/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13-blue.svg)](pyproject.toml)
[![MCP](https://img.shields.io/badge/MCP-Streamable%20HTTP-brightgreen.svg)](https://modelcontextprotocol.io/)

An [MCP server](https://modelcontextprotocol.io/) that wraps the
[api.phish.net v5](https://api.phish.net/) and
[phish.in v2](https://phish.in/) APIs behind a single typed tool surface.
Twelve tools across setlists, songs, jam-charts, reviews, and audio. Every
response is shaped through frozen Pydantic models so the wire format stays
stable across upstream API drift.

Built on FastMCP with Streamable HTTP transport. Designed to run on a trusted
LAN or behind a Tailscale ACL — there is no built-in MCP-level auth.

## Why

Today the Phish.net + phish.in ecosystem is a small set of unmaintained
wrappers and one-off scripts. Nobody combines the two cleanly. mcp-phish gives
any MCP-aware client (Claude Code, Claude Desktop, custom agents) a focused,
well-typed surface area for the questions phans actually ask: setlist for a
date, song debut and gap, audio URL for a track, jam-chart hits for a tour.

Source can change (live API → cached → vault). The Pydantic shapes returned to
MCP clients never do.

## Quick start

```bash
docker run --rm \
  -p 3705:3705 \
  -e STUB_MODE=true \
  ghcr.io/pete-builds/mcp-phish:latest
```

The server starts in **stub mode** by default. It returns realistic mock data
and requires no network access or API key. Register it with Claude Code:

```bash
claude mcp add phish --transport http --scope user --url http://localhost:3705/mcp
```

Then ask Claude: *"What was the setlist on 12/30/95?"* and you should get
back Madison Square Garden with the Mike's > Simple > Weekapaug groove.

To talk to the real APIs, register a free key at
[api.phish.net/keys/](https://api.phish.net/keys/) and flip stub mode off:

```bash
docker run --rm \
  -p 3705:3705 \
  -e STUB_MODE=false \
  -e PHISHNET_API_KEY=<your-key> \
  ghcr.io/pete-builds/mcp-phish:latest
```

## Tool reference

| Tool | Source | What it does |
|---|---|---|
| `search_shows` | phish.net | Search shows by year + venue + city/state/country. |
| `get_show` | phish.net | Full show: setlist, ratings, reviews count, venue. |
| `recent_shows` | phish.net | N most recent shows, most-recent-first. |
| `search_songs` | phish.net | Search the song catalog by title fragment. |
| `get_song` | phish.net | One song record: debut, last play, gap, total. |
| `song_history` | phish.net | Every performance of a song, most-recent-first. |
| `jam_chart` | phish.net | Editorially flagged notable jams. |
| `get_reviews` | phish.net | User reviews for a show. |
| `get_audio` | phish.in | Track list + MP3 URLs + durations for a show. |
| `get_track` | phish.in | One audio track by id. |
| `search_audio_tracks` | phish.in | Every recorded version of one song slug. |
| `health` | meta | Server status, throttle state, cache stats. |

Every tool returns a JSON string with the standard envelope:

```json
{"data": <typed payload>}
```

or, on failure:

```json
{
  "error": "human-readable message",
  "code": "UPSTREAM_DOWN | NOT_FOUND | INVALID_INPUT | RATE_LIMITED | INTERNAL",
  "details": { "...": "..." }
}
```

The Pydantic models in [`src/mcp_phish/models.py`](src/mcp_phish/models.py)
(``ShowSummary``, ``Show``, ``SetlistEntry``, ``Song``, ``Performance``,
``NotableJam``, ``Review``, ``Track``, ``ShowAudio``, ``Health``) are the
public contract. They are frozen with ``extra="forbid"`` so any upstream drift
becomes a validation error rather than a silent shape change.

## Stub mode vs real mode

| Mode | When to use | Behavior |
|---|---|---|
| **Stub** (`STUB_MODE=true`, default) | Development, demos, no API key yet | Realistic mock payloads for a small set of canonical shows (12/30/95 MSG, 11/17/97 Denver, 12/31/24 MSG). Every tool returns the same Pydantic shape it would in real mode. |
| **Real** (`STUB_MODE=false`) | Production with a phish.net API key | Talks HTTPS to api.phish.net v5 and phish.in v2. Requires `PHISHNET_API_KEY`; `PHISHIN_API_KEY` is optional. |

Switching modes is a config change, not a code change. Same twelve tools,
same response shapes.

## Caching

mcp-phish keeps an opaque key-value cache on disk
(`/data/phish-cache.db`, aiosqlite) keyed by `(endpoint, params_hash)`. A
single TTL governs every entry (`CACHE_TTL_SECONDS`, default `86400` =
24h). On a hit, no upstream call is made.

This cache is **not** a normalized data store. It just holds raw JSON
responses to keep us under the upstream rate limits and to make repeated
LLM-driven exploration fast. Treat it as ephemeral.

## Throttling

Every upstream call passes through a per-instance token bucket with a
configurable steady-state rate:

| Variable | Default | Notes |
|---|---|---|
| `THROTTLE_PHISHNET_RPS` | `5` | api.phish.net v5 requests per second. |
| `THROTTLE_PHISHIN_RPS` | `10` | phish.in v2 requests per second. |

The bucket is in-process. Multiple containers do not coordinate. Token state
is exposed in `health()` so a Claude Code session can see what's left.

## Configuration

All configuration is read from environment variables (and a `.env` file when
present). Pydantic validates at startup and fails fast on invalid values.

| Variable | Type | Default | Required | Notes |
|---|---|---|---|---|
| `STUB_MODE` | bool | `true` | no | When `false`, real-mode credentials are required. |
| `PHISHNET_API_KEY` | string | `""` | only in real mode | Free at api.phish.net/keys/. |
| `PHISHIN_API_KEY` | string | `""` | no | Optional; raises rate caps. |
| `PHISHNET_BASE_URL` | string | `https://api.phish.net/v5` | no | Override for testing. |
| `PHISHIN_BASE_URL` | string | `https://phish.in/api/v2` | no | Override for testing. |
| `CACHE_DB_PATH` | string | `/data/phish-cache.db` | no | aiosqlite file path. |
| `CACHE_TTL_SECONDS` | int | `86400` | no | 24h default. |
| `THROTTLE_PHISHNET_RPS` | float | `5.0` | no | Per-second steady rate. |
| `THROTTLE_PHISHIN_RPS` | float | `10.0` | no | Per-second steady rate. |
| `MCP_HOST` | string | `0.0.0.0` | no | Bind address. |
| `MCP_PORT` | int | `3705` | no | Listen port. |
| `LOG_LEVEL` | enum | `INFO` | no | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `LOG_FORMAT` | enum | `json` | no | `json` for production, `text` for local dev. |

A complete example lives in [`.env.example`](.env.example).

## MCP client setup

### Claude Code

```bash
claude mcp add phish --transport http --scope user --url http://<host>:3705/mcp
```

### Claude Desktop

```json
{
  "mcpServers": {
    "phish": {
      "transport": "streamable-http",
      "url": "http://<host>:3705/mcp"
    }
  }
}
```

### Generic config

Streamable HTTP at `http://<host>:3705/mcp`. Any MCP client supporting the
[Streamable HTTP transport](https://modelcontextprotocol.io/specification)
can connect.

## Architecture

```
+---------------------+     Streamable HTTP     +---------------------+
|  MCP Client         |  -------------------->  |  mcp-phish          |
|  (Claude Code, etc) |  <--------------------  |  (FastMCP server)   |
+---------------------+                         +----+--------------+-+
                                                     |              |
                                                     |              |
                                                     v              v
                                          +----------+--+   +-------+--------+
                                          | api.phish.net|   | phish.in/api/v2|
                                          |     v5       |   |                |
                                          +--------------+   +----------------+

                                                     ^
                                                     |
                                          +----------+----------+
                                          | aiosqlite KV cache  |
                                          |  /data/phish-cache  |
                                          +---------------------+
```

mcp-phish is a thin async proxy with a small cache: it translates MCP tool
calls into upstream REST calls, caches raw responses for the configured TTL,
and projects them into the public Pydantic shape. It does not store any
state beyond that cache. It does not call any cloud services other than the
two phish APIs.

## Security notes

- Run mcp-phish on a trusted LAN, on Tailscale, or behind a reverse proxy
  with auth. The server itself does **not** authenticate MCP clients.
- API keys live only in the container's environment. They are never logged,
  never echoed in responses, and never written to disk.
- The container runs as **UID 1000**, no shell, no home directory, with a
  **read-only root filesystem** (`/tmp` is `tmpfs`) and `no-new-privileges`.
- The `/data` cache volume is the only writable path.
- Python deps install with `pip --require-hashes` from a hash-locked
  `requirements.lock`. The base image will be pinned by digest before the
  first tagged release.
- Published images are multi-arch (amd64/arm64) with build provenance and
  SBOM via `docker/build-push-action`.

For vulnerability reports, see [SECURITY.md](SECURITY.md).

## Development

Requires Python 3.13+ and Docker.

```bash
# Clone + install dev deps
git clone https://github.com/pete-builds/mcp-phish.git
cd mcp-phish
python -m venv .venv && source .venv/bin/activate
pip install --require-hashes -r requirements-dev.lock
pip install -e . --no-deps

# Run the test suite
pytest

# Lint and format
ruff check src tests
ruff format src tests

# Type check (mypy strict)
mypy src/mcp_phish

# Run the server locally in stub mode
python -m mcp_phish.server

# Or build the image yourself
cp docker-compose.example.yml docker-compose.yml
docker compose up --build
```

### Updating dependencies

The `requirements.lock` and `requirements-dev.lock` files are hash-pinned.
Edit the matching `.in` file then regenerate:

```bash
uv pip compile requirements.in --output-file requirements.lock --generate-hashes --python-version 3.13
uv pip compile requirements-dev.in --output-file requirements-dev.lock --generate-hashes --python-version 3.13
```

Dependabot opens weekly PRs for `requirements.in`-level updates, the Docker
base image, and GitHub Actions versions.

## Roadmap

This server is **Phase 1** of a larger Phish data project. The Pydantic
contract documented above will stay byte-identical across the future
phases.

- **Phase 2** — Postgres vault + nightly ETL hydration.
- **Phase 3** — vault-backed read path for this MCP. Hot-window fallthrough
  reads recent shows live; older reads come from the vault.
- **Phase 4** — setlist-prediction game (separate repo).
- **Phase 5** — chat + dashboard UI over MCP (separate repo).

Phase status lives at <https://github.com/pete-builds/mcp-phish/issues>.

## Acknowledgments

Thanks to the [phish.net](https://phish.net) and [phish.in](https://phish.in)
operators for keeping the corpus public and machine-accessible. This wrapper
is unaffiliated with either project. Please respect their rate limits and
terms of service.

## License

[MIT](LICENSE).

## Contributing

Issues and pull requests welcome. Before opening a PR:

1. Make sure `ruff check`, `ruff format --check`, and `mypy src/mcp_phish` are clean.
2. Add or update tests; keep coverage at 80% or above.
3. Run `pytest` locally and confirm the suite passes.
4. Update `CHANGELOG.md` under an `[Unreleased]` heading.
