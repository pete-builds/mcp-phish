"""Hot-window decision: read a show live from phish.net, or from the vault.

A show is "hot" for a configurable window around when it happens; hot shows are
read live (the setlist is still being typed into phish.net during and just after
the show) and cold shows are served from the normalized vault.

The window is anchored to the END of the show's calendar day in US Eastern, not
to midnight UTC of the show date. A US-evening show plays in next-day UTC and
phish.net finalizes its setlist the following morning, so a midnight-UTC anchor
made the show ~25h "old" by the time the band took the stage and closed the
window exactly when a live read was needed (observed 2026-07-07, Kohl Center).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

# Phish tours the continental US; Eastern end-of-day is a safe, DST-aware anchor
# that covers every US-evening show plus the next-morning setlist finalization.
SHOW_DAY_TZ = ZoneInfo("America/New_York")


def is_hot(date_str: str, window_hours: float, now: datetime) -> bool:
    """Return True if the show on ``date_str`` should be read live.

    Args:
        date_str: show date (``YYYY-MM-DD``) or ISO datetime; only the date part
            is used.
        window_hours: how long after the end of the show day a show stays hot.
        now: current time, timezone-aware.

    A future or same-day show yields a negative age and is always hot.
    """
    try:
        show_date = datetime.fromisoformat(date_str).date()
    except (ValueError, OverflowError):
        return False
    end_of_show_day = datetime.combine(show_date + timedelta(days=1), time.min, tzinfo=SHOW_DAY_TZ)
    age_hours = (now - end_of_show_day).total_seconds() / 3600
    return age_hours < window_hours
