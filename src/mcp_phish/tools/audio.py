"""Audio tools: the phish.in side of the server.

``get_audio``, ``get_track``, ``search_audio_tracks``. These are the only
tools that read the phish.in upstream, and the only ones that return MP3
URLs, waveforms, and durations. That upstream boundary is the seam.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from mcp_phish.clients.phishin import PhishInError
from mcp_phish.mappers import phishin as pi_map
from mcp_phish.mappers import vault as vault_map
from mcp_phish.responses import err, ok
from mcp_phish.runtime import ServerContext

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger("mcp_phish.server")

__all__ = ["register"]


def register(mcp: FastMCP, ctx: ServerContext) -> None:
    """Register the audio-domain tools against ``mcp``."""

    @mcp.tool()
    async def get_audio(show_id_or_date: str) -> str:
        """Fetch the audio bundle for a show: track list, MP3 URLs, durations.

        Source is phish.in. Older or rare shows may have ``audio_status``
        equal to ``"missing"`` or ``"partial"``.

        Args:
            show_id_or_date: YYYY-MM-DD or phish.in numeric show id.

        Returns:
            JSON ``{"data": ShowAudio}``. ShowAudio fields: show_id, date,
            venue_name, venue_location, duration_ms, audio_status,
            album_zip_url, cover_art_url, tracks[].

        Idempotent. Example: ``get_audio("1997-11-17")``.
        """
        if not show_id_or_date:
            return err("show_id_or_date is required", "INVALID_INPUT")
        is_date_key = len(show_id_or_date) == 10 and show_id_or_date.count("-") == 2
        vr = await ctx.vault_reader()
        use_vault = vr is not None and not (is_date_key and ctx.is_hot_window(show_id_or_date))
        if use_vault:
            assert vr is not None
            try:
                show_row, tracks = await vr.get_audio(show_id_or_date)
                if show_row is None:
                    return err(f"show not found: {show_id_or_date}", "NOT_FOUND")
                return ok(vault_map.show_audio(show_row, tracks))
            except Exception:
                logger.exception(
                    "vault get_audio failed; falling back to live", extra={"key": show_id_or_date}
                )
        # Same hot-window short-TTL treatment as get_show: a same-night audio
        # bundle is still being uploaded track-by-track on phish.in.
        audio_hot_ttl = ctx.hot_ttl(show_id_or_date, is_date=is_date_key)
        try:
            payload = await ctx.cached_phishin(
                "get_show",
                {"key": show_id_or_date},
                lambda: ctx.phishin.get_show(show_id_or_date),
                ttl_override=audio_hot_ttl,
            )
            if not payload:
                return err(f"show not found: {show_id_or_date}", "NOT_FOUND")
            return ok(pi_map.show_audio(cast(dict[str, Any], payload)))
        except PhishInError as exc:
            logger.exception("get_audio failed", extra={"key": show_id_or_date})
            code = "NOT_FOUND" if "no show" in str(exc).lower() else "UPSTREAM_DOWN"
            return err(str(exc), code, upstream="phish.in")

    @mcp.tool()
    async def get_track(track_id: int) -> str:
        """Fetch one phish.in track by its numeric id.

        Args:
            track_id: phish.in track id (integer).

        Returns:
            JSON ``{"data": Track}``. Track fields: track_id, slug, title,
            show_id, show_date, set_name, position, duration_ms, mp3_url,
            waveform_image_url, venue_name, venue_location.

        Idempotent. Example: ``get_track(60001)``.
        """
        if not track_id:
            return err("track_id is required", "INVALID_INPUT")
        vr = await ctx.vault_reader()
        if vr is not None:
            try:
                row = await vr.get_track(int(track_id))
                if row is None:
                    return err(f"track not found: {track_id}", "NOT_FOUND")
                return ok(vault_map.track(row))
            except Exception:
                logger.exception(
                    "vault get_track failed; falling back to live", extra={"track_id": track_id}
                )
        try:
            payload = await ctx.cached_phishin(
                "get_track", {"id": int(track_id)}, lambda: ctx.phishin.get_track(int(track_id))
            )
            if not payload:
                return err(f"track not found: {track_id}", "NOT_FOUND")
            return ok(pi_map.track(cast(dict[str, Any], payload)))
        except PhishInError as exc:
            logger.exception("get_track failed", extra={"track_id": track_id})
            code = "NOT_FOUND" if "no track" in str(exc).lower() else "UPSTREAM_DOWN"
            return err(str(exc), code, upstream="phish.in")

    @mcp.tool()
    async def search_audio_tracks(song_slug: str, limit: int = 20) -> str:
        """Find every phish.in audio track for a given song slug, across shows.

        Useful for "give me every recorded version of X" questions.

        Args:
            song_slug: phish.net/phish.in slug (e.g. ``"tweezer"``).
            limit: Max rows. Default 20, capped at 200.

        Returns:
            JSON ``{"data": [Track, ...]}``.

        Idempotent. Example: ``search_audio_tracks("tweezer", limit=5)``.
        """
        if not song_slug:
            return err("song_slug is required", "INVALID_INPUT")
        capped = max(1, min(int(limit), 200))
        vr = await ctx.vault_reader()
        if vr is not None:
            try:
                rows = await vr.search_audio_tracks(song_slug=song_slug, limit=capped)
                return ok([vault_map.track(row) for row in rows])
            except Exception:
                logger.exception(
                    "vault search_audio_tracks failed; falling back to live",
                    extra={"slug": song_slug},
                )
        params = {"slug": song_slug, "per_page": capped}
        try:
            payload = await ctx.cached_phishin(
                "search_tracks", params, lambda: ctx.phishin.search_tracks(params)
            )
            rows_live = payload.get("tracks") or [] if isinstance(payload, dict) else []
            return ok([pi_map.track(row) for row in rows_live[:capped]])
        except PhishInError as exc:
            logger.exception("search_audio_tracks failed", extra={"slug": song_slug})
            return err(str(exc), "UPSTREAM_DOWN", upstream="phish.in")
