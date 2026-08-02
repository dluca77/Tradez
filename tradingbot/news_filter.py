"""Economic news / calendar filter.

Pulls from a pluggable calendar source. Ships with a static/offline calendar
you can populate; swap `fetch_events` for a real economic-calendar API by
setting NEWS_API_KEY and replacing the implementation.

Also supports a live-reloaded JSON file (`events_file`) as the event source,
so an external process — e.g. a scheduled agent that scans real-world news
and writes findings here — can update the blackout calendar while the bot
keeps running, without a restart. The file is re-read whenever its mtime
changes, checked on every is_blackout()/upcoming_within() call.

Expected JSON shape (a list of objects):
    [
      {"name": "Fed rate decision", "time": "2026-08-05T18:00:00",
       "impact": "extreme", "currencies": ["USD"], "note": "optional free text"}
    ]
`time` is UTC ISO-8601. `impact` is "high" or "extreme".
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


@dataclass
class NewsEvent:
    name: str
    time: datetime
    impact: str  # "high" | "extreme"
    currencies: list[str]
    note: str = ""


class NewsFilter:
    def __init__(
        self,
        pre_minutes: int,
        post_minutes: int,
        extreme_minutes: int,
        events_file: str | Path | None = None,
    ):
        self.pre_minutes = pre_minutes
        self.post_minutes = post_minutes
        self.extreme_minutes = extreme_minutes
        self._events: list[NewsEvent] = []
        self._events_file = Path(events_file) if events_file else None
        self._events_file_mtime: float | None = None

    def load_events(self, events: list[NewsEvent]) -> None:
        self._events = events

    def _reload_if_changed(self) -> None:
        if self._events_file is None or not self._events_file.exists():
            return
        mtime = self._events_file.stat().st_mtime
        if mtime == self._events_file_mtime:
            return
        self._events_file_mtime = mtime
        try:
            raw = json.loads(self._events_file.read_text())
            self._events = [
                NewsEvent(
                    name=e["name"],
                    time=datetime.fromisoformat(e["time"]),
                    impact=e["impact"],
                    currencies=e["currencies"],
                    note=e.get("note", ""),
                )
                for e in raw
            ]
        except (json.JSONDecodeError, KeyError, ValueError):
            # A malformed write (e.g. caught mid-write by the external
            # agent) must not crash the trading loop or wipe out a
            # previously-good calendar — just keep the last valid one and
            # try again next cycle.
            pass

    def is_blackout(self, currencies: list[str], now: datetime | None = None) -> tuple[bool, str | None]:
        self._reload_if_changed()
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
        self._reload_if_changed()
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
    "USOIL": ["USD"],
}
