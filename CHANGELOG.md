# Changelog

All notable changes to this project will be documented in this file. The
format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
