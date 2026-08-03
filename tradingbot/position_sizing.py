"""Position Sizing Engine: converts a risk percentage + stop distance into
a concrete order quantity, respecting the instrument's pip value."""
from __future__ import annotations

from tradingbot.broker.mock import INSTRUMENT_PROFILES
from tradingbot.models import PositionSizeResult

MIN_STOP_DISTANCE_PCT = 0.0005  # floor: at least 5 bps of entry price


def calculate_position_size(
    instrument: str, equity: float, risk_pct: float, entry_price: float, stop_loss: float
) -> PositionSizeResult:
    risk_amount = equity * risk_pct
    stop_distance = abs(entry_price - stop_loss)
    if stop_distance <= 0:
        return PositionSizeResult(0.0, 0.0, 0.0)

    # Floor the stop distance used for sizing. quantity = risk_amount /
    # stop_distance, so a near-zero ATR/stop distance (a genuinely quiet
    # candle, not a real invalidation level) blows the quantity up hugely
    # and makes every subsequent R-multiple reading swing wildly (a tiny
    # price tick reads as several R either way) — the realized loss/gain
    # then no longer reflects the intended risk_pct at all.
    min_distance = entry_price * MIN_STOP_DISTANCE_PCT
    sizing_distance = max(stop_distance, min_distance)

    profile = INSTRUMENT_PROFILES.get(instrument)
    # For simplicity: quantity in "units" such that (stop_distance * quantity) == risk_amount.
    # This works uniformly for FX pairs quoted as price-per-unit and for metals/indices as CFDs.
    quantity = risk_amount / sizing_distance
    return PositionSizeResult(lots_or_units=quantity, risk_amount=risk_amount, stop_distance=stop_distance)
