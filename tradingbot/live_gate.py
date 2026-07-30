"""LiveTradingGate: the single choke point that must approve before any
real order is ever placed. Live trading cannot bypass this — `run_live.py`
refuses to start the controller against a real broker unless every check
passes and the user has explicitly set `live_trading_enabled: true` and
confirmed the activation interactively.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tradingbot.broker.base import BrokerInterface
from tradingbot.config import AppConfig
from tradingbot.database import Database
from tradingbot.performance import compute_performance


@dataclass
class GateResult:
    approved: bool
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


MIN_PAPER_TRADES = 30
MIN_PAPER_WIN_RATE = 0.40
MIN_PAPER_PROFIT_FACTOR = 1.0


class LiveTradingGate:
    def __init__(self, cfg: AppConfig, db: Database):
        self.cfg = cfg
        self.db = db

    async def evaluate(self, broker: BrokerInterface) -> GateResult:
        checks: dict[str, bool] = {}
        reasons: list[str] = []

        checks["config_live_trading_enabled"] = bool(self.cfg.live_trading_enabled)
        if not checks["config_live_trading_enabled"]:
            reasons.append("config.yaml live_trading_enabled is false")

        connected = await broker.connect()
        checks["broker_connection_validated"] = connected
        if not connected:
            reasons.append("broker connection/credentials could not be validated")

        if connected:
            account = await broker.get_account_info()
            checks["broker_account_reachable"] = account.balance >= 0
        else:
            checks["broker_account_reachable"] = False

        perf = compute_performance(self.db)
        checks["paper_trading_sample_size"] = perf.total_trades >= MIN_PAPER_TRADES
        if not checks["paper_trading_sample_size"]:
            reasons.append(f"paper trading has only {perf.total_trades} closed trades, need >= {MIN_PAPER_TRADES}")

        checks["paper_trading_win_rate"] = perf.win_rate >= MIN_PAPER_WIN_RATE
        if not checks["paper_trading_win_rate"]:
            reasons.append(f"paper trading win rate {perf.win_rate:.1%} below {MIN_PAPER_WIN_RATE:.0%}")

        checks["paper_trading_profit_factor"] = perf.profit_factor >= MIN_PAPER_PROFIT_FACTOR
        if not checks["paper_trading_profit_factor"]:
            reasons.append(f"paper trading profit factor {perf.profit_factor:.2f} below {MIN_PAPER_PROFIT_FACTOR}")

        risk = self.cfg.risk
        checks["risk_limits_configured"] = (
            0 < risk.default_risk_pct <= risk.max_risk_pct <= risk.absolute_max_risk_pct <= 0.01
            and not (risk.martingale_forbidden is False)
        )
        if not checks["risk_limits_configured"]:
            reasons.append("risk limits in config.yaml are missing, inverted, or exceed the safety ceiling")

        approved = all(checks.values())
        self.db.log_decision("live_trading_gate", {"approved": approved, "checks": checks, "reasons": reasons})
        return GateResult(approved=approved, checks=checks, reasons=reasons)
