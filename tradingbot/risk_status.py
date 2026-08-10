"""Shared "is a risk gate active right now" status check, used by both the
dashboard banner and the Discord bot's /status command. Mirrors
risk_manager.evaluate()'s account-wide checks (same order, same
thresholds) purely to report current status - never blocks anything
itself."""
from __future__ import annotations

from datetime import datetime

from tradingbot.config import RiskConfig
from tradingbot.risk_manager import SessionState


def active_risk_gate(state: SessionState, cfg: RiskConfig, equity: float) -> dict | None:
    now = datetime.utcnow()

    if state.kill_switch:
        return {"icon": "\U0001F6A8", "text": "NOODSTOP ACTIEF", "cls": "gate-error"}

    if state.cooldown_until and now < state.cooldown_until:
        until = state.cooldown_until.strftime("%H:%M")
        return {"icon": "⏸", "text": f"Cooldown na verliezen op rij, actief tot {until} UTC", "cls": "gate-warn"}

    month_dd = (state.peak_equity - equity) / state.peak_equity if state.peak_equity else 0.0
    if month_dd >= cfg.max_monthly_drawdown_pct:
        return {"icon": "\U0001F6A8", "text": f"Maandelijkse drawdown-limiet geraakt ({month_dd:.1%})", "cls": "gate-error"}

    day_pnl_pct = (equity - state.day_start_equity) / state.day_start_equity if state.day_start_equity else 0.0
    if day_pnl_pct <= -cfg.max_daily_loss_pct:
        return {
            "icon": "\U0001F512",
            "text": f"Dagverlieslimiet geraakt ({day_pnl_pct:.1%}) — alleen confidence ≥{cfg.daily_loss_limit_min_confidence:.0f} nog toegestaan",
            "cls": "gate-warn",
        }

    week_pnl_pct = (equity - state.week_start_equity) / state.week_start_equity if state.week_start_equity else 0.0
    if week_pnl_pct <= -cfg.max_weekly_loss_pct:
        return {"icon": "\U0001F512", "text": f"Weekverlieslimiet geraakt ({week_pnl_pct:.1%})", "cls": "gate-error"}

    day_peak_gain = (state.day_peak_equity - state.day_start_equity) / state.day_start_equity if state.day_start_equity else 0.0
    if day_peak_gain > 0:
        giveback = (state.day_peak_equity - equity) / (state.day_peak_equity - state.day_start_equity)
        if giveback >= cfg.daily_profit_giveback_pct and day_peak_gain >= cfg.daily_profit_soft_lock_pct:
            return {"icon": "\U0001F512", "text": f"Te veel van dagwinst teruggegeven ({giveback:.0%}) — dag geblokkeerd", "cls": "gate-warn"}

    if day_pnl_pct >= cfg.daily_profit_lock_pct:
        return {
            "icon": "\U0001F3AF",
            "text": f"Dagdoel gehaald ({day_pnl_pct:.1%}) — alleen confidence ≥{cfg.daily_profit_lock_min_confidence:.0f} nog toegestaan",
            "cls": "gate-lock",
        }

    return None
