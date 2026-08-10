"""Self-Optimization Module.

Adjusts strategy parameters within safe, pre-defined bounds based on
historical performance. Never allowed to loosen hard risk limits, enable
martingale, or disable safety features — those are structurally impossible
to reach from this module because it only exposes bounded setters.
"""
from __future__ import annotations

import json
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
        self._last_evaluated_trades = 0

    def optimize(self, perf: PerformanceSummary) -> list[str]:
        changes: list[str] = []

        if perf.total_trades < self.min_sample_size:
            return changes

        # Only react once per newly closed trade, not on every scan cycle.
        # Without this, the exact same overall stats (e.g. profit_factor
        # still < 1.0 from an old trade) got re-penalized every ~15s scan,
        # walking risk_multiplier down to its floor within minutes even
        # though nothing new had actually happened.
        if perf.total_trades == self._last_evaluated_trades:
            return changes
        self._last_evaluated_trades = perf.total_trades

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

        # Reduce risk multiplier if profit factor is bad, but let it recover
        # (never above the 1.0 baseline) once performance has genuinely
        # turned around — otherwise a multiplier walked down to its floor
        # by old/stale results stayed pinned there forever, with no way
        # back up even after a long stretch of real, proven good trades.
        # This is not martingale: it only ever climbs back toward the
        # original 1.0 baseline, never above it, and only in response to
        # an already-positive track record, never after losses.
        if perf.profit_factor < 1.0:
            old = self.state.risk_multiplier
            new = max(old - MAX_RISK_STEP, RISK_MULTIPLIER_FLOOR)
            if new != old:
                self.state.risk_multiplier = new
                self.db.log_parameter_change("risk_multiplier", old, new, "profit factor below 1.0")
                changes.append(f"reduced risk multiplier {old:.2f} -> {new:.2f}")
        elif perf.profit_factor > 1.2 and perf.win_rate > 0.5:
            old = self.state.risk_multiplier
            new = min(old + MAX_RISK_STEP, 1.0)
            if new != old:
                self.state.risk_multiplier = new
                self.db.log_parameter_change("risk_multiplier", old, new, "profit factor recovered above 1.2")
                changes.append(f"restored risk multiplier {old:.2f} -> {new:.2f}")

        self.db.save_optimization_state(
            min_confidence=self.state.min_confidence,
            disabled_strategies=self.state.disabled_strategies,
            risk_multiplier=self.state.risk_multiplier,
            last_evaluated_trades=self._last_evaluated_trades,
        )
        return changes

    def restore(self) -> None:
        """Reload optimization state persisted before the last shutdown, so a
        restart doesn't silently undo tightening (or disabled strategies)
        the module had applied based on real historical performance."""
        row = self.db.load_optimization_state()
        if not row:
            return
        self.state.min_confidence = row["min_confidence"]
        self.state.disabled_strategies = set(json.loads(row["disabled_strategies"]))
        self.state.risk_multiplier = row["risk_multiplier"]
        self._last_evaluated_trades = row["last_evaluated_trades"]
