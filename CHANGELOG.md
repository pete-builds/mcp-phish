# Changelog

All notable changes to this project will be documented in this file. The
format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

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
