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

# The USD-exposure model above gives every equity index 0 exposure, so it
# never penalizes them against EACH OTHER - but major indices move together
# on global risk-on/risk-off sentiment regardless of currency. Observed
# 2026-08-05: simultaneous same-direction UK100+JPN225 shorts both lost
# together, combining to a single loss (-1103.87) far bigger than either
# position's own risk budget, with no size reduction applied because this
# group didn't exist. A separate, additive correlation channel - same
# 0.25-per-position penalty curve as the USD model, taking the more
# restrictive of the two rather than compounding them.
EQUITY_INDEX_GROUP = {"NAS100", "SPX500", "UK100", "JPN225", "GER40"}


def usd_exposure(direction: Direction, instrument: str) -> float:
    base = USD_EXPOSURE_WHEN_LONG.get(instrument, 0)
    return base if direction == Direction.LONG else -base


def combined_usd_exposure(open_positions: list[Position], candidate_direction: Direction, candidate_instrument: str, candidate_risk: float) -> float:
    total = 0.0
    for pos in open_positions:
        total += usd_exposure(pos.direction, pos.instrument) * pos.risk_amount
    total += usd_exposure(candidate_direction, candidate_instrument) * candidate_risk
    return total


def _usd_correlation_penalty(open_positions: list[Position], candidate_direction: Direction, candidate_instrument: str) -> float:
    exposures = [usd_exposure(p.direction, p.instrument) for p in open_positions]
    candidate_exp = usd_exposure(candidate_direction, candidate_instrument)
    if candidate_exp == 0 or not exposures:
        return 1.0
    same_direction_count = sum(1 for e in exposures if e != 0 and (e > 0) == (candidate_exp > 0))
    if same_direction_count == 0:
        return 1.0
    return max(0.2, 1.0 - 0.25 * same_direction_count)


def _index_correlation_penalty(open_positions: list[Position], candidate_direction: Direction, candidate_instrument: str) -> float:
    if candidate_instrument not in EQUITY_INDEX_GROUP:
        return 1.0
    same_direction_count = sum(
        1 for p in open_positions
        if p.instrument in EQUITY_INDEX_GROUP
        and p.instrument != candidate_instrument
        and p.direction == candidate_direction
    )
    if same_direction_count == 0:
        return 1.0
    return max(0.2, 1.0 - 0.25 * same_direction_count)


def correlation_penalty(open_positions: list[Position], candidate_direction: Direction, candidate_instrument: str) -> float:
    """Returns a 0..1 penalty factor; 1 = fully independent, 0 = fully redundant.

    Two independent correlation channels (USD exposure, equity-index
    co-movement) can each flag the same candidate - taking the more
    restrictive (lower) of the two avoids compounding them into an
    unrealistically small size when only one channel actually applies.
    """
    usd_penalty = _usd_correlation_penalty(open_positions, candidate_direction, candidate_instrument)
    index_penalty = _index_correlation_penalty(open_positions, candidate_direction, candidate_instrument)
    return min(usd_penalty, index_penalty)
