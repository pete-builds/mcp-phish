"""Hot-window anchoring tests.

The regression these guard: a US-evening show plays in next-day UTC, so a
window anchored to midnight UTC of the show date closed ~25h too early and a
live in-progress show was served as a cold vault read (empty setlist).
"""

from __future__ import annotations

from datetime import UTC, datetime

from mcp_phish.hotwindow import is_hot

WINDOW = 24.0


def _utc(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def test_live_evening_show_is_hot() -> None:
    """2026-07-07 Kohl Center (Central tz). Showtime is 02:17 UTC on 07-08,
    which is 26h past midnight-UTC of the show date — the old anchor called
    this cold. It must be hot."""
    now = _utc(2026, 7, 8, 2, 17)
    assert is_hot("2026-07-07", WINDOW, now) is True


def test_next_morning_finalization_is_hot() -> None:
    """phish.net finalizes the setlist the morning after; still hot."""
    now = _utc(2026, 7, 8, 14, 0)  # ~9am ET the day after the show
    assert is_hot("2026-07-07", WINDOW, now) is True


def test_show_from_three_days_ago_is_cold() -> None:
    now = _utc(2026, 7, 10, 12, 0)
    assert is_hot("2026-07-07", WINDOW, now) is False


def test_future_show_is_hot() -> None:
    now = _utc(2026, 7, 7, 12, 0)
    assert is_hot("2026-07-09", WINDOW, now) is True


def test_late_pacific_show_still_hot_while_playing() -> None:
    """A Pacific show plays into the early UTC hours of the next day; a read
    during the encore must still be hot."""
    # Show 2026-07-07 in PT; encore around 06:00 UTC on 07-08.
    now = _utc(2026, 7, 8, 6, 0)
    assert is_hot("2026-07-07", WINDOW, now) is True


def test_window_edge_just_inside_and_outside() -> None:
    # End of show day (ET) for 2026-07-07 = 2026-07-08 00:00 EDT = 04:00 UTC.
    # Hot until 04:00 UTC on 07-08 + 24h = 04:00 UTC on 07-09.
    assert is_hot("2026-07-07", WINDOW, _utc(2026, 7, 9, 3, 59)) is True
    assert is_hot("2026-07-07", WINDOW, _utc(2026, 7, 9, 4, 1)) is False


def test_malformed_date_is_not_hot() -> None:
    now = _utc(2026, 7, 8, 2, 17)
    assert is_hot("not-a-date", WINDOW, now) is False
    assert is_hot("", WINDOW, now) is False


def test_accepts_iso_datetime_uses_date_part() -> None:
    now = _utc(2026, 7, 8, 2, 17)
    assert is_hot("2026-07-07T20:00:00", WINDOW, now) is True
