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
        local_open = {row["id"] for row in self.db.fetch_open_trades()}
        broker_ids = {p.id for p in broker_positions}

        missing_locally = broker_ids - local_open
        missing_at_broker = local_open - broker_ids

        for pid in missing_locally:
            log.warning("recovery.position_found_at_broker_not_local", position_id=pid)
        for tid in missing_at_broker:
            log.warning("recovery.trade_local_not_at_broker_marking_closed", trade_id=tid)
            self.db.record_trade_close(tid, 0.0, 0.0, 0.0, "reconciliation_broker_missing")

        for pos in broker_positions:
            if pos.stop_loss is None or pos.stop_loss == 0:
                log.error("recovery.missing_stop_loss", position_id=pos.id, instrument=pos.instrument)

        log.info("recovery.complete", broker_positions=len(broker_positions))
        return broker_positions
