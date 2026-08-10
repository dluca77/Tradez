"""Trading session quality scoring (London/NY overlap prioritized, rollover avoided)."""
from __future__ import annotations

from datetime import datetime, time


def session_quality(now: datetime | None = None) -> tuple[float, str]:
    now = now or datetime.utcnow()
    t = now.time()

    def between(a: time, b: time) -> bool:
        return a <= t <= b

    if between(time(12, 0), time(16, 0)):
        return 1.0, "london_ny_overlap"
    if between(time(7, 0), time(11, 59)):
        return 0.8, "london"
    if between(time(13, 30), time(16, 0)):
        return 0.85, "ny_open"
    if between(time(21, 0), time(23, 59)) or between(time(0, 0), time(1, 0)):
        return 0.1, "rollover"
    if between(time(1, 0), time(6, 59)):
        return 0.35, "illiquid_asia_tail"
    return 0.5, "normal"


def instrument_cooldown_active(
    instrument: str,
    cooldowns: dict[str, dict] | None,
    now: datetime | None = None,
) -> tuple[bool, str | None]:
    """Per-instrument time-of-day skip, e.g. to sit out a volatile cash-open
    window for one specific instrument. Distinct from session_quality() above
    (a global score that nudges confidence for every instrument) - this is a
    hard skip, scoped to whichever instrument+window is configured.
    """
    if not cooldowns or instrument not in cooldowns:
        return False, None
    window = cooldowns[instrument]
    start = time.fromisoformat(window["start_utc"])
    end = time.fromisoformat(window["end_utc"])
    now = now or datetime.utcnow()
    t = now.time()
    in_window = start <= t <= end if start <= end else (t >= start or t <= end)
    if in_window:
        return True, f"session cooldown {window['start_utc']}-{window['end_utc']} UTC"
    return False, None
