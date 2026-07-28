# mcp-phish

[![CI](https://github.com/pete-builds/mcp-phish/actions/workflows/ci.yml/badge.svg)](https://github.com/pete-builds/mcp-phish/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13-blue.svg)](pyproject.toml)
[![MCP](https://img.shields.io/badge/MCP-Streamable%20HTTP-brightgreen.svg)](https://modelcontextprotocol.io/)

An [MCP server](https://modelcontextprotocol.io/) that wraps the
[api.phish.net v5](https://api.phish.net/) and
[phish.in v2](https://phish.in/) APIs behind a single typed tool surface.
Sixteen tools across setlists, songs, jam-charts, reviews, audio, venues, and
catalog statistics. Every response is shaped through frozen Pydantic models so
the wire format stays stable across upstream API drift.

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

Source column reads: **vault → API** means the tool prefers the Postgres vault
and falls through to the live API inside the hot window (or when the vault is
disabled). **vault only** means the tool has no upstream equivalent and returns
`VAULT_DISABLED` when the vault is off. See
[Vault read path](#vault-read-path).

| Tool | Source | What it does |
|---|---|---|
| `search_shows` | vault → phish.net | Search shows by year + venue + city/state/country. |
| `get_show` | vault → phish.net | Full show: setlist, ratings, reviews count, venue. |
| `recent_shows` | vault → phish.net | N most recent shows, most-recent-first. |
| `search_songs` | vault → phish.net | Search the song catalog by title fragment or community nickname. Surfaces current gap. |
| `get_song` | vault → phish.net | One song record: debut, last play, gap, total. |
| `validate_song_slugs` | vault → phish.net | Partition 1-50 candidate slugs into `valid` and `unknown`. One round-trip on vault. |
| `song_history` | vault → phish.net | Every performance of a song, most-recent-first. |
| `jam_chart` | vault → phish.net | Editorially flagged notable jams. |
| `get_reviews` | vault → phish.net | User reviews for a show. |
| `get_audio` | vault → phish.in | Track list + MP3 URLs + durations for a show. |
| `get_track` | vault → phish.in | One audio track by id. |
| `search_audio_tracks` | vault → phish.in | Every recorded version of one song slug. |
| `venue_history` | vault only | All shows at a venue, most recent first. |
| `songs_by_gap` | vault only | Songs ranked by current gap, descending. Bust-out candidates. |
| `stats_overview` | vault only | Catalog-wide roll-up: totals, most-played, biggest gaps, rarest, recent debuts, longest shows. |
| `health` | meta | Server status, throttle state, cache stats, vault freshness. |

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
(``Venue``, ``ShowSummary``, ``Show``, ``SetlistEntry``, ``SongSummary``,
``Song``, ``Performance``, ``NotableJam``, ``Review``, ``Track``,
``ShowAudio``, ``VenueShow``, ``SongGap``, ``SlugValidation``, ``TopSong``,
``DebutSong``, ``LongShow``, ``StatsOverview``, ``Health``) are the public
contract. They are frozen with ``extra="forbid"`` so any upstream drift
becomes a validation error rather than a silent shape change.

## Stub mode vs real mode

| Mode | When to use | Behavior |
|---|---|---|
| **Stub** (`STUB_MODE=true`, default) | Development, demos, no API key yet | Realistic mock payloads for a small set of canonical shows (12/30/95 MSG, 11/17/97 Denver, 12/31/24 MSG). Every API-backed tool returns the same Pydantic shape it would in real mode. |
| **Real** (`STUB_MODE=false`) | Production with a phish.net API key | Talks HTTPS to api.phish.net v5 and phish.in v2. Requires `PHISHNET_API_KEY`; `PHISHIN_API_KEY` is optional. |

Switching modes is a config change, not a code change. Same sixteen tools,
same response shapes.

`STUB_MODE` and `VAULT_ENABLED` are independent switches. Stub mode only
governs the two upstream HTTP clients; it does not stand in for the vault. The
three vault-only tools (`venue_history`, `songs_by_gap`, `stats_overview`)
return `VAULT_DISABLED` whenever `VAULT_ENABLED=false`, in stub mode and real
mode alike.

## Vault read path

Reads are served from a [phish-vault](https://github.com/pete-builds) Postgres
database when `VAULT_ENABLED=true`, with a live-API fallthrough for shows
inside the **hot window**. This is implemented and in production, not planned.

How a read resolves:

1. **Vault disabled** (`VAULT_ENABLED=false`, the default) — every tool goes
   straight to the upstream API exactly as it did before the vault existed.
   Vault-only tools return `VAULT_DISABLED`.
2. **Vault enabled, show outside the hot window** — served from Postgres. No
   upstream call.
3. **Vault enabled, show inside the hot window** — a setlist is typed into
   phish.net live during and just after a show, so recent dates bypass the
   vault and read the API under a short cache TTL
   (`HOT_WINDOW_CACHE_TTL_SECONDS`) so repeat polls see fresh setlists. The
   window is anchored to the **end of the show day in US/Eastern**, not
   midnight UTC, and spans `VAULT_HOT_WINDOW_HOURS`.
4. **Vault enabled but stale** — if the last successful ETL run is older than
   `VAULT_MAX_STALE_HOURS`, `health()` reports `vault.stale: true` and an
   overall status of `degraded`. Note this is a **reporting** signal only:
   read tools keep serving vault rows regardless of staleness. Monitor
   `health()` if stale data matters to your client.

Vault rows and API payloads project through separate mappers into the same
frozen Pydantic models, so the wire format does not tell a client which source
answered. `health()` reports vault connectivity and ETL freshness when you need
to know.

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
| `HOT_WINDOW_CACHE_TTL_SECONDS` | int | see `config.py` | no | Short TTL applied to live reads inside the hot window so polls see fresh setlists. |
| `THROTTLE_PHISHNET_RPS` | float | `5.0` | no | Per-second steady rate. |
| `THROTTLE_PHISHIN_RPS` | float | `10.0` | no | Per-second steady rate. |
| `VAULT_ENABLED` | bool | `false` | no | When `true`, read tools prefer the Postgres vault. See [Vault read path](#vault-read-path). |
| `VAULT_HOT_WINDOW_HOURS` | int | see `config.py` | no | Hours after end-of-show-day (ET) during which reads bypass the vault and go live. |
| `VAULT_MAX_STALE_HOURS` | int | see `config.py` | no | ETL age past which `health()` reports the vault `stale`/`degraded`. Reporting only; does not gate reads. |
| `PG_HOST` | string | `postgres` | only when vault enabled | phish-vault Postgres host. |
| `PG_PORT` | int | `5432` | no | Postgres port. |
| `PG_DB` | string | `phish` | no | Vault database name. |
| `PG_USER` | string | `phish` | no | Vault role. |
| `PG_PASSWORD` | secret | `""` | only when vault enabled | Never logged; omitted from `health()`. |
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
+---------------------+                         +----------+----------+
                                                           |
                                        VAULT_ENABLED?     |
                            +------------------------------+
                            |                              |
                   true, outside hot window        false, or inside
                            |                        hot window
                            v                              |
                 +----------+----------+                   v
                 |  phish-vault        |        +----------+----------+
                 |  Postgres           |        | aiosqlite KV cache  |
                 |  (nightly ETL)      |        |  /data/phish-cache  |
                 +---------------------+        +----------+----------+
                                                           | miss
                                                           v
                                            +--------------+--+   +----------------+
                                            | api.phish.net v5 |   | phish.in/api/v2|
                                            +------------------+   +----------------+
```

mcp-phish is an async read proxy over two sources. With the vault disabled it
is a thin API proxy with a small cache: it translates MCP tool calls into
upstream REST calls, caches raw responses for the configured TTL, and projects
them into the public Pydantic shape. With the vault enabled it reads
[phish-vault](https://github.com/pete-builds) Postgres for everything outside
the hot window and falls through to the same API path for recent shows.

Either way mcp-phish owns no durable state of its own beyond the KV cache: the
vault is a separate service hydrated by its own nightly ETL, and mcp-phish only
ever reads from it (`vault.py` issues `SELECT` and nothing else). Grant the
`PG_USER` role read-only privileges to enforce that at the database, too. It
calls no cloud services other than
the two phish APIs.

## Security notes

- Run mcp-phish on a trusted LAN, on Tailscale, or behind a reverse proxy
  with auth. The server itself does **not** authenticate MCP clients.
- API keys live only in the container's environment. They are never logged,
  never echoed in responses, and never written to disk.
- The container runs as **UID 1000**, no shell, no home directory, with a
  **read-only root filesystem** (`/tmp` is `tmpfs`) and `no-new-privileges`.
- The `/data` cache volume is the only writable path.
- Python deps install with `pip --require-hashes` from a hash-locked
  `requirements.lock`. Both Dockerfile stages pin the base image by digest,
  refreshed by Dependabot.
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

This server is part of a larger Phish data project. The Pydantic contract
documented above stays byte-identical across phases.

- **Phase 1 — done.** Typed MCP surface over the phish.net and phish.in APIs,
  with an aiosqlite response cache and per-upstream throttling.
- **Phase 2 — done** (separate repo). Postgres vault + nightly ETL hydration.
- **Phase 3 — done.** Vault-backed read path for this MCP, with hot-window
  fallthrough so recent shows read live and older reads come from the vault.
  All sixteen tools consult the vault when `VAULT_ENABLED=true`; three of them
  (`venue_history`, `songs_by_gap`, `stats_overview`) are vault-only. See
  [Vault read path](#vault-read-path) for the resolution order and
  [Configuration](#configuration) for the `VAULT_*` / `PG_*` variables.
- **Phase 4 — in progress** (separate repo). Setlist-prediction game.
- **Phase 5 — planned** (separate repo). Chat + dashboard UI over MCP.

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
