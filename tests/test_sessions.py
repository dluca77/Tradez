from __future__ import annotations

from datetime import datetime

from tradingbot.sessions import instrument_cooldown_active


COOLDOWNS = {"UK100": {"start_utc": "07:00", "end_utc": "07:15"}}


def test_cooldown_active_inside_window():
    now = datetime(2026, 8, 10, 7, 5)
    active, reason = instrument_cooldown_active("UK100", COOLDOWNS, now)
    assert active
    assert reason is not None


def test_cooldown_inactive_outside_window():
    now = datetime(2026, 8, 10, 7, 30)
    active, reason = instrument_cooldown_active("UK100", COOLDOWNS, now)
    assert not active
    assert reason is None


def test_cooldown_inactive_for_uncovered_instrument():
    now = datetime(2026, 8, 10, 7, 5)
    active, _ = instrument_cooldown_active("XAUUSD", COOLDOWNS, now)
    assert not active


def test_cooldown_boundaries_inclusive():
    start = datetime(2026, 8, 10, 7, 0)
    end = datetime(2026, 8, 10, 7, 15)
    assert instrument_cooldown_active("UK100", COOLDOWNS, start)[0]
    assert instrument_cooldown_active("UK100", COOLDOWNS, end)[0]


def test_no_cooldowns_configured():
    active, _ = instrument_cooldown_active("UK100", {}, datetime(2026, 8, 10, 7, 5))
    assert not active
    active, _ = instrument_cooldown_active("UK100", None, datetime(2026, 8, 10, 7, 5))
    assert not active
