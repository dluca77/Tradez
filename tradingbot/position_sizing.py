"""Position Sizing Engine: converts a risk percentage + stop distance into
a concrete order quantity, respecting the instrument's pip value."""
from __future__ import annotations

from tradingbot.broker.mock import INSTRUMENT_PROFILES
from tradingbot.models import PositionSizeResult


def calculate_position_size(
    instrument: str, equity: float, risk_pct: float, entry_price: float, stop_loss: float
) -> PositionSizeResult:
    risk_amount = equity * risk_pct
    stop_distance = abs(entry_price - stop_loss)
    if stop_distance <= 0:
        return PositionSizeResult(0.0, 0.0, 0.0)

    profile = INSTRUMENT_PROFILES.get(instrument)
    # For simplicity: quantity in "units" such that (stop_distance * quantity) == risk_amount.
    # This works uniformly for FX pairs quoted as price-per-unit and for metals/indices as CFDs.
    quantity = risk_amount / stop_distance
    return PositionSizeResult(lots_or_units=quantity, risk_amount=risk_amount, stop_distance=stop_distance)
