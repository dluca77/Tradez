"""Self-Optimization Module.

Adjusts strategy parameters within safe, pre-defined bounds based on
historical performance. Never allowed to loosen hard risk limits, enable
martingale, or disable safety features — those are structurally impossible
to reach from this module because it only exposes bounded setters.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tradingbot.database import Database
from tradingbot.performance import PerformanceSummary


@dataclass
class OptimizationState:
    min_confidence: float = 75.0
    disabled_strategies: set[str] = field(default_factory=set)
    risk_multiplier: float = 1.0  # only ever <= 1.0


MIN_CONFIDENCE_FLOOR = 70.0
MIN_CONFIDENCE_CEILING = 92.0
RISK_MULTIPLIER_FLOOR = 0.25
MAX_CONFIDENCE_STEP = 5.0
MAX_RISK_STEP = 0.05


class SelfOptimizationModule:
    def __init__(self, db: Database, min_sample_size: int = 30):
        self.db = db
        self.min_sample_size = min_sample_size
        self.state = OptimizationState()

    def optimize(self, perf: PerformanceSummary) -> list[str]:
        changes: list[str] = []

        if perf.total_trades < self.min_sample_size:
            return changes

        # Disable strategies with a poor track record and enough samples.
        for name, stats in perf.by_strategy.items():
            if stats["trades"] >= 15 and stats["win_rate"] < 0.35 and stats["avg_r"] < 0:
                if name not in self.state.disabled_strategies:
                    self.state.disabled_strategies.add(name)
                    self.db.log_parameter_change("disabled_strategies", "", name, "poor historical performance")
                    changes.append(f"disabled strategy {name}")

        # Tighten confidence floor if overall win rate is weak.
        if perf.win_rate < 0.4:
            old = self.state.min_confidence
            new = min(old + min(MAX_CONFIDENCE_STEP, 5.0), MIN_CONFIDENCE_CEILING)
            if new != old:
                self.state.min_confidence = new
                self.db.log_parameter_change("min_confidence", old, new, "win rate below 40%")
                changes.append(f"raised min confidence {old:.0f} -> {new:.0f}")

        # Slowly relax confidence floor back down after strong performance,
        # but never below the configured floor.
        elif perf.win_rate > 0.6 and perf.profit_factor > 1.5:
            old = self.state.min_confidence
            new = max(old - 2.0, MIN_CONFIDENCE_FLOOR)
            if new != old:
                self.state.min_confidence = new
                self.db.log_parameter_change("min_confidence", old, new, "strong recent performance")
                changes.append(f"lowered min confidence {old:.0f} -> {new:.0f}")

        # Reduce overall risk multiplier (never increase) if profit factor is bad.
        if perf.profit_factor < 1.0:
            old = self.state.risk_multiplier
            new = max(old - MAX_RISK_STEP, RISK_MULTIPLIER_FLOOR)
            if new != old:
                self.state.risk_multiplier = new
                self.db.log_parameter_change("risk_multiplier", old, new, "profit factor below 1.0")
                changes.append(f"reduced risk multiplier {old:.2f} -> {new:.2f}")

        return changes
