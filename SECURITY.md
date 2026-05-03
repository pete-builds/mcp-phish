# Security Policy

## Reporting a vulnerability

If you find a security issue, please **do not** open a public GitHub issue.
Instead, open a private security advisory on this repository:

https://github.com/pete-builds/mcp-phish/security/advisories/new

I will respond within 7 days. Please include:

- A description of the issue and its impact
- Steps to reproduce (or a proof-of-concept)
- The version (image tag or commit SHA) you tested against
- Any suggested mitigation, if you have one

## Supported versions

Only the most recent minor release receives security fixes. The current
supported version is whatever is tagged latest on the
[Releases page](https://github.com/pete-builds/mcp-phish/releases).

## Threat model

mcp-phish is designed to run on a trusted LAN or behind a Tailscale ACL and
talk outbound to api.phish.net + phish.in. The server itself does **not**
authenticate incoming MCP connections; access control is the responsibility
of the host network.

The container:

- Runs as a non-root user (UID 1000), no shell, no home directory
- Uses a read-only root filesystem (with a small `tmpfs` for `/tmp`)
- Mounts a single writable volume at `/data` for the local cache
- Drops `no-new-privileges` and runs no capabilities beyond default
- Pins Python deps via `pip --require-hashes` from a hash-locked lockfile
- Never logs API keys (a redacting JSON formatter scrubs known sensitive
  keys defensively)
- Calls only `api.phish.net` and `phish.in`. No other outbound HTTPS.

API keys are read from environment variables (`PHISHNET_API_KEY`,
`PHISHIN_API_KEY`). They are never written to disk and never returned in MCP
responses.

## Cache file

The aiosqlite cache at `/data/phish-cache.db` stores raw JSON responses from
both upstreams keyed by `(endpoint, params_hash)`. It contains only public
Phish data (setlists, song catalog, audio URLs). It does NOT contain any
authentication material. It is safe to back up but you may want to delete
it on container teardown if disk hygiene matters.

## What this server does NOT do

- It does not authenticate MCP clients. Run it on a trusted network or
  behind a reverse proxy with auth.
- It does not redistribute audio. `mp3_url` and `album_zip_url` fields are
  references to phish.in's CDN, used at the client's discretion.
- It does not auto-update. Pin a specific tag in your `docker-compose.yml`.
