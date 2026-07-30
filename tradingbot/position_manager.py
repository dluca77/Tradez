"""Position Management Engine: manages every open position continuously.

Handles break-even moves, partial take-profits, ATR/technical trailing
stops, time-based exits, and invalidation exits. A stop-loss is NEVER moved
further from entry than its current value — only tightened.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog

from tradingbot.broker.base import BrokerInterface
from tradingbot.models import Direction, Position

log = structlog.get_logger(__name__)


@dataclass
class ManagementAction:
    action: str
    detail: str


def _r_multiple(pos: Position, current_price: float) -> float:
    risk_per_unit = abs(pos.entry_price - pos.initial_stop_loss)
    if risk_per_unit <= 0:
        return 0.0
    direction_mult = 1 if pos.direction == Direction.LONG else -1
    return (current_price - pos.entry_price) * direction_mult / risk_per_unit


def _tighten_only(pos: Position, new_stop: float) -> float:
    """Never move stop-loss further away from entry (no giving losers more room)."""
    if pos.direction == Direction.LONG:
        return max(pos.stop_loss, new_stop)
    return min(pos.stop_loss, new_stop)


class PositionManagementEngine:
    def __init__(self, broker: BrokerInterface, max_hold: timedelta = timedelta(hours=6)):
        self.broker = broker
        self.max_hold = max_hold

    async def manage(self, pos: Position, current_price: float, current_atr: float, signal_still_valid: bool) -> list[ManagementAction]:
        actions: list[ManagementAction] = []
        r = _r_multiple(pos, current_price)

        # 1) Break-even at 1R
        if r >= 1.0 and not pos.breakeven_moved:
            new_stop = _tighten_only(pos, pos.entry_price)
            if new_stop != pos.stop_loss:
                await self.broker.modify_position(pos.id, stop_loss=new_stop)
                pos.stop_loss = new_stop
                pos.breakeven_moved = True
                actions.append(ManagementAction("breakeven", f"stop moved to entry at {r:.2f}R"))

        # 2) Partial profit taking at 1.5R and 2R
        if r >= 1.5 and pos.quantity > pos.initial_quantity * 0.55:
            await self.broker.close_position(pos.id, fraction=0.3 / (pos.quantity / pos.initial_quantity))
            pos.quantity *= 0.7
            actions.append(ManagementAction("partial_close", f"closed 30% at {r:.2f}R"))
        elif r >= 2.0 and pos.quantity > pos.initial_quantity * 0.25:
            await self.broker.close_position(pos.id, fraction=0.3 / (pos.quantity / pos.initial_quantity))
            pos.quantity *= 0.7
            actions.append(ManagementAction("partial_close", f"closed additional 30% at {r:.2f}R"))

        # 3) ATR trailing stop once in profit beyond 1R
        if r >= 1.0:
            direction_mult = 1 if pos.direction == Direction.LONG else -1
            trail_price = current_price - direction_mult * 1.2 * current_atr
            new_stop = _tighten_only(pos, trail_price)
            if new_stop != pos.stop_loss:
                await self.broker.modify_position(pos.id, stop_loss=new_stop)
                pos.stop_loss = new_stop
                pos.trailing_active = True
                actions.append(ManagementAction("trailing_stop", f"trailed to {new_stop:.5f}"))

        # 4) Signal invalidated -> exit
        if not signal_still_valid:
            await self.broker.close_position(pos.id, fraction=1.0)
            actions.append(ManagementAction("exit_invalidated", "original signal no longer valid"))
            return actions

        # 5) Time-based exit
        if datetime.utcnow() - pos.opened_at > self.max_hold:
            await self.broker.close_position(pos.id, fraction=1.0)
            actions.append(ManagementAction("exit_time", "max hold time exceeded"))

        return actions

    async def emergency_close_all(self, positions: list[Position], reason: str) -> None:
        for pos in positions:
            await self.broker.close_position(pos.id, fraction=1.0)
            log.warning("position.emergency_close", instrument=pos.instrument, reason=reason)
