"""Safety and Kill-Switch Module: hard stops that override everything else."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog

from tradingbot.database import Database
from tradingbot.risk_manager import SessionState

log = structlog.get_logger(__name__)


@dataclass
class SafetyStatus:
    ok: bool
    reason: str | None = None


class SafetyModule:
    def __init__(self, db: Database, max_drawdown_pct: float, stale_data_seconds: int = 90):
        self.db = db
        self.max_drawdown_pct = max_drawdown_pct
        self.stale_data_seconds = stale_data_seconds
        self._last_data_ts: datetime | None = None

    def mark_data_received(self) -> None:
        self._last_data_ts = datetime.utcnow()

    def stale_data(self) -> bool:
        if self._last_data_ts is None:
            return False
        return (datetime.utcnow() - self._last_data_ts) > timedelta(seconds=self.stale_data_seconds)

    def check_drawdown(self, peak_equity: float, equity: float, state: SessionState) -> SafetyStatus:
        dd = (peak_equity - equity) / peak_equity if peak_equity else 0.0
        if dd >= self.max_drawdown_pct:
            state.kill_switch = True
            self.db.log_safety_event("kill_switch", f"drawdown {dd:.2%} >= max {self.max_drawdown_pct:.2%}")
            log.error("safety.kill_switch", drawdown=dd)
            return SafetyStatus(False, "max_drawdown_kill_switch")
        return SafetyStatus(True)

    def check_broker_connection(self, connected: bool) -> SafetyStatus:
        if not connected:
            self.db.log_safety_event("broker_disconnect", "broker reported disconnected")
            return SafetyStatus(False, "broker_disconnected")
        return SafetyStatus(True)

    def check_stale_data(self) -> SafetyStatus:
        if self.stale_data():
            self.db.log_safety_event("stale_data", "no fresh market data received")
            return SafetyStatus(False, "stale_data")
        return SafetyStatus(True)

    def check_spread(self, spread_pips: float, avg_spread_pips: float, max_multiple: float = 3.0) -> SafetyStatus:
        if spread_pips > avg_spread_pips * max_multiple:
            return SafetyStatus(False, "spread_protection")
        return SafetyStatus(True)

    def manual_kill_switch(self, state: SessionState, reason: str = "manual") -> None:
        state.kill_switch = True
        self.db.log_safety_event("manual_kill_switch", reason)
