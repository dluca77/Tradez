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
