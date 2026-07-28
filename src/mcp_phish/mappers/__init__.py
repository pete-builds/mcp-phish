"""Projection layer: untyped source rows in, frozen public models out.

One module per source, because each source has its own field names and its
own quirks:

* :mod:`mcp_phish.mappers.phishnet` — api.phish.net v5 JSON dicts
* :mod:`mcp_phish.mappers.phishin`  — phish.in v2 JSON dicts
* :mod:`mcp_phish.mappers.vault`    — Postgres vault rows (asyncpg records)
* :mod:`mcp_phish.mappers.coerce`   — the scalar coercions all three share

Nothing is re-exported here on purpose. Import from the source module you
actually mean, so a call site always says which upstream shape it is reading.
"""

from __future__ import annotations
