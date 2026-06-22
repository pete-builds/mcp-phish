# Changelog

All notable changes to this project will be documented in this file. The
format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed (internal refactor — no behavior change)

- **Split the `server.py` god-file into data-domain modules.** The single
  1437-line `build_server` factory (15 `@mcp.tool()` defs plus ~30 projection
  helpers) is now a thin `build_server` + register-modules shell (~150 lines).
  Tool definitions moved to `modules/{shows,songs,audio,extras,health}.py`,
  each exposing a `register(mcp, ctx)` entrypoint dispatched by
  `modules/__init__.py:register_modules`. The pure row→model projection
  functions (`_phishnet_*`, `_phishin_*`, `_vault_*`, `_safe_*`) and the client
  protocols live in `mappers.py`; the response-envelope helpers (`_ok`, `_err`,
  `_to_jsonable`) live in `_common.py`. Shared runtime state (clients, cache,
  throttles, vault wiring, the cache-fetch and hot-window helpers) is bundled
  into `_context.ServerContext`. Every tool is wrapped in an `@audited(...)`
  decorator (`modules/_audit.py`) that emits a debug audit record per call,
  mirroring the mcp-unifi module pattern. The public tool surface, names,
  signatures, return shapes, and `build_server` signature are byte-identical;
  all 143 tests pass unchanged.

### Fixed

- **Dockerfile Python version drift.** Both build stages were pinned to
  `python:3.14-slim` while `requirements.lock` is compiled for Python 3.13
  (`uv pip compile ... --python-version 3.13`). Pinned the image back to
  `python:3.13-slim` so the runtime matches the lockfile. Dependabot will
  refresh the digest on its next weekly run.

### Added (batch validation + community aliases)

- **`validate_song_slugs(slugs)` tool.** Partitions a list of up to 50
  candidate slugs into `valid` and `unknown`. Single SELECT against the
  vault when enabled; falls back to per-slug `get_song` lookups against the
  live phish.net API when not. Designed for downstream form-validation
  flows (e.g. phish-game's date-pick screen).
- **`SlugValidation` model.** New frozen Pydantic shape:
  `{valid: list[str], unknown: list[str]}`. Existing models remain
  byte-identical.
- **`VaultReader.validate_slugs(slugs)`.** Single round-trip set lookup
  against `songs.slug` via `slug = ANY($1::text[])`.

### Changed

- **`search_songs` is now alias-aware.** The vault SQL LEFT JOINs the new
  community-curated `song_aliases_local` table (seeded by phish-vault
  migration 003). Fans typing "yem", "rnr", "dwd" find the canonical
  song. The live (non-vault) path is unchanged.

### Added (Phase 3 — Vault read path)

- **Vault-backed read path.** When `VAULT_ENABLED=true`, all read tools
  serve answers from the phish-vault Postgres database instead of the live
  phish.net / phish.in APIs. Hot-window shows (newer than
  `VAULT_HOT_WINDOW_HOURS`, default 24h) always read live so very recent
  shows do not lag the daily ETL.
- **Two new vault-only analytical tools:**
  - `venue_history(venue_slug, limit)` — every show at a venue, newest first.
  - `songs_by_gap(limit)` — songs ordered by current gap (shows since last
    play), descending. Most-overdue songs at the top.
- **VaultReader** (`src/mcp_phish/vault.py`) — async read facade over
  Postgres via `asyncpg`. Pool created lazily on first vault read so the
  server boots without blocking on the database.
- **Health degrades on stale ETL.** `health()` now reports `vault.enabled`,
  `vault.last_etl_run`, `vault.staleness_hours`, and `vault.stale`. When the
  last ETL run is older than `VAULT_MAX_STALE_HOURS` (default 36h),
  `status` flips to `"degraded"` and `vault.stale=true`.
- **Vault failures fall back to live.** Any exception in the vault path is
  logged and the live API path is used, so the MCP stays available if the
  database is down.
- **New env vars** (all default to disabled, so behavior is unchanged for
  anyone running without vault): `VAULT_ENABLED`, `VAULT_HOT_WINDOW_HOURS`,
  `VAULT_MAX_STALE_HOURS`, `PG_HOST`, `PG_PORT`, `PG_DB`, `PG_USER`,
  `PG_PASSWORD`. See `.env.example`.
- **Compose joins the `phish-vault_default` external network** so
  `mcp-phish` can resolve the `postgres` container by name. No-op when
  vault is disabled.

### Changed

- `Health` model gained a required `vault: VaultHealth` field. Existing
  shapes (`Show`, `Song`, `Track`, etc.) are byte-identical to Phase 1.
- `build_server()` accepts new keyword-only args `vault_reader` and
  `vault_pool` (both `None` by default). Backward-compatible.
- `asyncpg>=0.29` added as a runtime dependency. Regenerate
  `requirements.lock` on nix1 with
  `uv pip compile requirements.in -o requirements.lock --generate-hashes
  --python-version 3.13 --python-platform linux` before deploy.

### Added (Phase 1 — initial release)

- Initial 12-tool surface across phish.net v5 and phish.in v2:
  - `search_shows`, `get_show`, `recent_shows`
  - `search_songs`, `get_song`, `song_history`, `jam_chart`
  - `get_reviews`
  - `get_audio`, `get_track`, `search_audio_tracks`
  - `health`
- Frozen Pydantic public-contract models (`ShowSummary`, `Show`,
  `SetlistEntry`, `Song`, `Performance`, `NotableJam`, `Review`, `Track`,
  `ShowAudio`, `Venue`, `Health`).
- Opaque KV cache in `aiosqlite` keyed by `(endpoint, params_hash)`, single
  24h TTL by default.
- Per-instance token-bucket throttle for both upstreams, configurable via
  `THROTTLE_PHISHNET_RPS` and `THROTTLE_PHISHIN_RPS`. State exposed in
  `health()`.
- Stub mode (default) returns realistic data without network or API key.
- Streamable HTTP transport on port 3705.
- Standard error contract (`{error, code, details}`) on every tool failure.
- Hardened Dockerfile (UID 1000, read-only rootfs, no-new-privileges,
  hash-locked deps), docker-compose with cache volume, CI gates (ruff +
  mypy strict + pytest 80% min + Trivy fs+image), Dependabot.
