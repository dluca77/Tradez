"""Correlation Manager: prevents hidden/duplicated USD (or other) exposure."""
from __future__ import annotations

from tradingbot.models import Direction, Position

# Static correlation groups. +1 means the instrument moves with USD strength
# in the same direction as a "long USD" bias when the pair is shorted (since
# these are XXX/USD or USD/XXX pairs). Simplification: define each
# instrument's directional exposure to USD when going LONG the instrument.
USD_EXPOSURE_WHEN_LONG = {
    "EURUSD": -1, "GBPUSD": -1, "AUDUSD": -1,      # long EURUSD = short USD
    "USDJPY": 1, "USDCHF": 1, "USDCAD": 1,          # long USDJPY = long USD
    "XAUUSD": -1, "XAGUSD": -1,                     # gold/silver up = usually USD weak
    "NAS100": 0, "SPX500": 0, "GER40": 0,
}


def usd_exposure(direction: Direction, instrument: str) -> float:
    base = USD_EXPOSURE_WHEN_LONG.get(instrument, 0)
    return base if direction == Direction.LONG else -base


def combined_usd_exposure(open_positions: list[Position], candidate_direction: Direction, candidate_instrument: str, candidate_risk: float) -> float:
    total = 0.0
    for pos in open_positions:
        total += usd_exposure(pos.direction, pos.instrument) * pos.risk_amount
    total += usd_exposure(candidate_direction, candidate_instrument) * candidate_risk
    return total


def correlation_penalty(open_positions: list[Position], candidate_direction: Direction, candidate_instrument: str) -> float:
    """Returns a 0..1 penalty factor; 1 = fully independent, 0 = fully redundant."""
    exposures = [usd_exposure(p.direction, p.instrument) for p in open_positions]
    candidate_exp = usd_exposure(candidate_direction, candidate_instrument)
    if candidate_exp == 0 or not exposures:
        return 1.0
    same_direction_count = sum(1 for e in exposures if e != 0 and (e > 0) == (candidate_exp > 0))
    if same_direction_count == 0:
        return 1.0
    return max(0.2, 1.0 - 0.25 * same_direction_count)
