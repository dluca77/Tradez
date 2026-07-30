"""Economic news / calendar filter.

Pulls from a pluggable calendar source. Ships with a static/offline calendar
you can populate; swap `fetch_events` for a real economic-calendar API by
setting NEWS_API_KEY and replacing the implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class NewsEvent:
    name: str
    time: datetime
    impact: str  # "high" | "extreme"
    currencies: list[str]


class NewsFilter:
    def __init__(self, pre_minutes: int, post_minutes: int, extreme_minutes: int):
        self.pre_minutes = pre_minutes
        self.post_minutes = post_minutes
        self.extreme_minutes = extreme_minutes
        self._events: list[NewsEvent] = []

    def load_events(self, events: list[NewsEvent]) -> None:
        self._events = events

    def is_blackout(self, currencies: list[str], now: datetime | None = None) -> tuple[bool, str | None]:
        now = now or datetime.utcnow()
        for ev in self._events:
            if not set(ev.currencies) & set(currencies):
                continue
            pre = timedelta(minutes=self.extreme_minutes if ev.impact == "extreme" else self.pre_minutes)
            post = timedelta(minutes=self.extreme_minutes if ev.impact == "extreme" else self.post_minutes)
            if ev.time - pre <= now <= ev.time + post:
                return True, f"{ev.name} ({ev.impact}) blackout window"
        return False, None

    def upcoming_within(self, currencies: list[str], minutes: int, now: datetime | None = None) -> NewsEvent | None:
        now = now or datetime.utcnow()
        window = now + timedelta(minutes=minutes)
        for ev in self._events:
            if set(ev.currencies) & set(currencies) and now <= ev.time <= window:
                return ev
        return None


INSTRUMENT_CURRENCIES = {
    "XAUUSD": ["USD"], "XAGUSD": ["USD"],
    "EURUSD": ["EUR", "USD"], "GBPUSD": ["GBP", "USD"],
    "USDJPY": ["USD", "JPY"], "USDCHF": ["USD", "CHF"],
    "AUDUSD": ["AUD", "USD"], "USDCAD": ["USD", "CAD"],
    "NAS100": ["USD"], "SPX500": ["USD"], "GER40": ["EUR"],
}
