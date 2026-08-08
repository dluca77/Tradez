"""Recovery and Reconciliation Service.

Runs once at startup (and can be re-run any time) to bring the local state
back in sync with the broker after a crash, restart, or disconnect.
"""
from __future__ import annotations

import structlog

from tradingbot.broker.base import BrokerInterface
from tradingbot.database import Database
from tradingbot.models import Position

log = structlog.get_logger(__name__)


class RecoveryService:
    def __init__(self, broker: BrokerInterface, db: Database):
        self.broker = broker
        self.db = db

    async def recover(self) -> list[Position]:
        connected = await self.broker.connect()
        if not connected:
            log.error("recovery.broker_connect_failed")
            return []

        account = await self.broker.get_account_info()
        log.info("recovery.account", balance=account.balance, equity=account.equity)

        broker_positions = await self.broker.get_open_positions()
        local_rows = {row["id"]: row for row in self.db.fetch_open_trades()}
        local_open = set(local_rows)
        broker_ids = {p.id for p in broker_positions}

        missing_locally = broker_ids - local_open
        missing_at_broker = local_open - broker_ids

        for pid in missing_locally:
            log.warning("recovery.position_found_at_broker_not_local", position_id=pid)
        for tid in missing_at_broker:
            # Fetch the broker's real exit price/pnl (history_deals_get) the
            # same way the live vanish-detection does — a flat 0.0/0.0
            # placeholder here (the previous behavior) silently records a
            # real win or loss as a no-op break-even trade, which is wrong
            # in the same direction as never recording it at all.
            row = local_rows[tid]
            result = await self.broker.get_closed_position_result(tid)
            if result is not None:
                exit_price, pnl = result
                direction_mult = 1 if row["direction"] == "long" else -1
                risk_per_unit = abs(row["entry_price"] - row["stop_loss"]) if row["stop_loss"] else 0.0
                r_mult = (
                    (exit_price - row["entry_price"]) * direction_mult / risk_per_unit
                    if risk_per_unit else 0.0
                )
                exit_reason = "reconciliation_broker_missing"
            else:
                exit_price, pnl, r_mult = row["entry_price"], 0.0, 0.0
                exit_reason = "reconciliation_broker_missing_unknown_pnl"
            log.warning(
                "recovery.trade_local_not_at_broker_marking_closed",
                trade_id=tid, pnl=pnl, exit_reason=exit_reason,
            )
            self.db.record_trade_close(tid, exit_price, pnl, r_mult, exit_reason)

        for pos in broker_positions:
            if pos.stop_loss is None or pos.stop_loss == 0:
                log.error("recovery.missing_stop_loss", position_id=pos.id, instrument=pos.instrument)

        log.info("recovery.complete", broker_positions=len(broker_positions))
        return broker_positions
