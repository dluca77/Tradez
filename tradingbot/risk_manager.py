"""Dynamic Risk Manager.

Determines the per-trade risk percentage and enforces every hard safety
limit. Risk can only ever be scaled DOWN after losses (never up) — no
martingale. All hard limits here are ceilings; the self-optimization module
may tighten them further but must never loosen them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tradingbot.config import RiskConfig
from tradingbot.models import RiskDecision


@dataclass
class SessionState:
    """Rolling state the risk manager needs, updated by the controller."""
    day_start_equity: float
    week_start_equity: float
    month_start_equity: float
    peak_equity: float
    day_peak_equity: float
    consecutive_losses: int = 0
    trades_today: int = 0
    trades_this_hour: int = 0
    hour_window_start: datetime = field(default_factory=datetime.utcnow)
    cooldown_until: datetime | None = None
    kill_switch: bool = False
    risk_scale: float = 1.0  # multiplier applied on top of confidence-derived risk


def confidence_to_base_risk(confidence: float, cfg: RiskConfig) -> float:
    if confidence >= 95:
        return cfg.max_risk_pct
    if confidence >= 90:
        return min(0.0050, cfg.max_risk_pct)
    if confidence >= 80:
        return min(0.0025, cfg.max_risk_pct)
    if confidence >= 70:
        return min(0.0010, cfg.max_risk_pct)
    return 0.0


class DynamicRiskManager:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg

    def evaluate(
        self,
        confidence: float,
        equity: float,
        state: SessionState,
        open_positions_count: int,
        total_open_risk_pct: float,
        correlation_penalty: float,
        volatility_factor: float,   # 1.0 normal, >1 elevated -> reduce size
        spread_factor: float,       # 1.0 normal, >1 wide -> reduce size
        now: datetime | None = None,
    ) -> RiskDecision:
        now = now or datetime.utcnow()
        reasons: list[str] = []

        if state.kill_switch:
            return RiskDecision(0.0, ["kill switch active"], blocked=True, block_reason="kill_switch")

        if state.cooldown_until:
            if now < state.cooldown_until:
                return RiskDecision(0.0, [f"cooldown active until {state.cooldown_until}"], blocked=True, block_reason="cooldown")
            # Cooldown period has elapsed — automatically clear it and the
            # consecutive-loss streak that triggered it. Without this, a
            # consecutive-losses block never expired on its own: it only
            # ever cleared on the next WINNING trade, but no new trades
            # could open to produce that win — a permanent deadlock that
            # "Hervat" (resume) couldn't fix either, since resume only
            # un-pauses and has nothing to do with this counter.
            state.cooldown_until = None
            state.consecutive_losses = 0

        day_dd = (state.day_peak_equity - equity) / state.day_peak_equity if state.day_peak_equity else 0.0
        month_dd = (state.peak_equity - equity) / state.peak_equity if state.peak_equity else 0.0

        if month_dd >= self.cfg.max_monthly_drawdown_pct:
            return RiskDecision(0.0, [f"monthly drawdown {month_dd:.2%} >= limit"], blocked=True, block_reason="max_drawdown")

        day_pnl_pct = (equity - state.day_start_equity) / state.day_start_equity if state.day_start_equity else 0.0
        if day_pnl_pct <= -self.cfg.max_daily_loss_pct:
            return RiskDecision(0.0, [f"daily loss {day_pnl_pct:.2%} hit limit"], blocked=True, block_reason="daily_loss_limit")

        week_pnl_pct = (equity - state.week_start_equity) / state.week_start_equity if state.week_start_equity else 0.0
        if week_pnl_pct <= -self.cfg.max_weekly_loss_pct:
            return RiskDecision(0.0, [f"weekly loss {week_pnl_pct:.2%} hit limit"], blocked=True, block_reason="weekly_loss_limit")

        day_peak_gain = (state.day_peak_equity - state.day_start_equity) / state.day_start_equity if state.day_start_equity else 0.0
        if day_peak_gain > 0:
            giveback = (state.day_peak_equity - equity) / (state.day_peak_equity - state.day_start_equity)
            if giveback >= self.cfg.daily_profit_giveback_pct and day_peak_gain >= self.cfg.daily_profit_soft_lock_pct:
                return RiskDecision(0.0, ["giving back too much of day's profit"], blocked=True, block_reason="profit_giveback")
        if day_pnl_pct >= self.cfg.daily_profit_lock_pct:
            return RiskDecision(0.0, [f"daily profit target {day_pnl_pct:.2%} reached, locking in gains"], blocked=True, block_reason="daily_profit_lock")

        if open_positions_count >= self.cfg.max_concurrent_positions:
            return RiskDecision(0.0, ["max concurrent positions reached"], blocked=True, block_reason="max_positions")

        if state.trades_today >= self.cfg.max_trades_per_day:
            return RiskDecision(0.0, ["max trades per day reached"], blocked=True, block_reason="max_trades_day")

        if state.trades_this_hour >= self.cfg.max_trades_per_hour:
            return RiskDecision(0.0, ["max trades per hour reached"], blocked=True, block_reason="max_trades_hour")

        if state.consecutive_losses >= self.cfg.max_consecutive_losses:
            return RiskDecision(0.0, ["max consecutive losses reached, cooling down"], blocked=True, block_reason="consecutive_losses")

        if total_open_risk_pct >= self.cfg.max_total_open_risk_pct:
            return RiskDecision(0.0, ["max total open risk reached"], blocked=True, block_reason="max_open_risk")

        base_risk = confidence_to_base_risk(confidence, self.cfg)
        if base_risk <= 0:
            return RiskDecision(0.0, ["confidence below minimum risk tier"], blocked=True, block_reason="low_confidence")

        # Never scale risk UP after losses (no martingale) — only down.
        loss_scale = 1.0
        if state.consecutive_losses == 2:
            loss_scale = 0.75
            reasons.append("2 consecutive losses -> risk x0.75")
        elif state.consecutive_losses == 3:
            loss_scale = 0.50
            reasons.append("3 consecutive losses -> risk x0.50")

        if day_dd >= 0.03:
            loss_scale = min(loss_scale, 0.5)
            reasons.append("3%+ intraday drawdown -> risk halved")

        risk_pct = base_risk * loss_scale * state.risk_scale * correlation_penalty
        risk_pct /= max(volatility_factor, 1.0)
        risk_pct /= max(spread_factor, 1.0)

        risk_pct = min(risk_pct, self.cfg.absolute_max_risk_pct)
        risk_pct = max(risk_pct, 0.0)

        if risk_pct < self.cfg.min_risk_pct * 0.5:
            return RiskDecision(0.0, reasons + ["computed risk below viable minimum"], blocked=True, block_reason="risk_too_small")

        reasons.append(f"final risk {risk_pct:.4%}")
        return RiskDecision(risk_pct=risk_pct, reasons=reasons, blocked=False)
