"""Transaction cost estimation: spread + commission + slippage + swap."""
from __future__ import annotations

from dataclasses import dataclass

from tradingbot.broker.mock import INSTRUMENT_PROFILES


@dataclass
class CostEstimate:
    spread_cost: float
    commission: float
    slippage_estimate: float
    swap_estimate: float

    @property
    def total(self) -> float:
        return self.spread_cost + self.commission + self.slippage_estimate + self.swap_estimate


def estimate_costs(instrument: str, quantity: float, spread: float, hold_hours: float = 0.5) -> CostEstimate:
    profile = INSTRUMENT_PROFILES.get(instrument, {})
    spread_cost = spread * quantity
    commission = quantity * 0.00002 * (profile.get("price", 1.0))  # tiny synthetic commission
    slippage = spread_cost * 0.3
    swap = 0.0 if hold_hours < 20 else quantity * profile.get("price", 1.0) * 0.00002
    return CostEstimate(spread_cost, commission, slippage, swap)


def cost_to_profit_ratio(cost_total: float, expected_gross_profit: float) -> float:
    if expected_gross_profit <= 0:
        return 1.0
    return cost_total / expected_gross_profit
